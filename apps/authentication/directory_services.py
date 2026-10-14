"""Directory configurations, and the accounts that come from a directory."""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.authentication import ldap as directory
from apps.authentication.models import LdapConfig
from apps.core.enums import AccountTypeChoices, AuthProviderChoices
from apps.core.exceptions import NotFoundError, ValidationError
from apps.rbac import selectors as rbac_selectors
from apps.rbac import services as rbac_services

logger = logging.getLogger(__name__)

ROLE_SOURCE = "directory"
IN_USE = 9015


# ── Accounts ─────────────────────────────────────────────────────────────────
def directory_account_type() -> str:
    configured = getattr(settings, "LDAP_ACCOUNT_TYPE", "") or AccountTypeChoices.Internal.value
    return configured if configured in AccountTypeChoices.values() else AccountTypeChoices.Internal.value


def names_from_email(email: str) -> tuple[str, str]:
    """Best-effort first/last name from an e-mail local part (``jane.doe`` →)."""
    local = email.split("@", 1)[0]
    parts = [part for part in local.replace("_", ".").replace("-", ".").split(".") if part]
    first = parts[0].capitalize() if parts else ""
    last = parts[1].capitalize() if len(parts) > 1 else ""
    return first, last


def default_role(config=None):
    """The role a directory account starts with: the configuration's, else the
    one named by ``LDAP_DEFAULT_ROLE``."""
    if config is not None and config.default_role_id:
        return config.default_role
    name = getattr(settings, "LDAP_DEFAULT_ROLE", "") or ""
    return rbac_selectors.get_role_by_name(name) if name else None


def ensure_directory_account(*, email: str, config=None, user=None, active: bool = True):
    """Create or align the local account of a person who lives in a directory.

    Every directory sign-in keeps the account aligned: provider = LDAP, the
    configured account type, verified (the directory vouches for the address).
    The starting role is granted once.
    """
    User = get_user_model()
    created = user is None
    if created:
        first, last = names_from_email(email)
        user = User(
            email=email,
            first_name=first,
            last_name=last,
            account_type=directory_account_type(),
            auth_provider=AuthProviderChoices.LDAP.value,
            is_active=active,
            is_verified=True,
        )
        user.set_unusable_password()  # the password is checked by the directory, never here
        user.save()
    else:
        changed = []
        if user.auth_provider != AuthProviderChoices.LDAP.value:
            user.auth_provider = AuthProviderChoices.LDAP.value
            changed.append("auth_provider")
        if not user.is_verified:
            user.is_verified = True
            changed.append("is_verified")
        if changed:
            user.save(update_fields=changed)

    role = default_role(config)
    if role is not None and user.pk:
        rbac_services.grant_role(user, role, source=ROLE_SOURCE, source_ref=getattr(config, "uid", ""))
    return user


def on_directory_login(user) -> None:
    """Hook for the global directory backend (django-auth-ldap ``populate_user``)."""
    if user is None:
        return
    changed = False
    if user.account_type != directory_account_type() and user.auth_provider != AuthProviderChoices.LDAP.value:
        user.account_type = directory_account_type()
        changed = True
    if user.auth_provider != AuthProviderChoices.LDAP.value:
        user.auth_provider = AuthProviderChoices.LDAP.value
        changed = True
    if not user.is_verified:
        user.is_verified = True
        changed = True
    if changed and user.pk:
        user.save()
    role = default_role()
    if role is not None and user.pk:
        rbac_services.grant_role(user, role, source=ROLE_SOURCE)


@transaction.atomic
def pull_users(*, config_uid, actor) -> list:
    """Create an account for every person in a directory who has none.

    The accounts start **inactive**: an administrator reviews and activates
    them. On first sign-in the person authenticates against the directory.
    Returns the newly created accounts (existing ones are left as they are).
    """
    User = get_user_model()
    config = get_config(config_uid)
    if not config.is_active:
        raise ValidationError("This directory is switched off.", code=9002)

    emails = directory.fetch_directory_emails(config)
    actor_email = (getattr(actor, "email", "") or "").lower()
    candidates = [email for email in emails if email != actor_email]
    if not candidates:
        return []

    # One query to find which already exist (across *all* rows, deleted
    # included), instead of a SELECT per candidate.
    existing = {
        email.lower()
        for email in User.all_objects.filter(email__in=candidates).values_list("email", flat=True)
    }
    new_users = []
    for email in candidates:
        if email in existing:
            continue
        first, last = names_from_email(email)
        candidate = User(
            email=email,
            first_name=first,
            last_name=last,
            account_type=directory_account_type(),
            auth_provider=AuthProviderChoices.LDAP.value,
            is_active=False,  # an administrator activates after review
            is_verified=True,  # identity vouched for by the directory
            created_by=actor if getattr(actor, "is_authenticated", False) else None,
        )
        candidate.set_unusable_password()
        new_users.append(candidate)

    created = User.objects.bulk_create(new_users, batch_size=500)
    # bulk_create may not return primary keys on every database: re-read.
    created = list(User.all_objects.filter(email__in=[user.email for user in created]))

    role = default_role(config)
    if role is not None:
        for user in created:
            rbac_services.grant_role(user, role, source=ROLE_SOURCE, source_ref=config.uid)

    logger.info(
        "[pull_users] %s: created %s (of %s candidates, %s existed) by %s",
        config,
        len(created),
        len(candidates),
        len(existing),
        getattr(actor, "pk", None),
    )
    return created


# ── Configurations ───────────────────────────────────────────────────────────
def get_config(uid) -> LdapConfig:
    config = LdapConfig.objects.filter(uid=uid).first()
    if config is None:
        raise NotFoundError("Directory configuration not found")
    return config


def _clean_url(ldap_url: str) -> str:
    ldap_url = (ldap_url or "").strip()
    if not ldap_url.startswith(("ldap://", "ldaps://")):
        raise ValidationError("LDAP URL must start with ldap:// or ldaps://", code=9002)
    return ldap_url


def _clean_domain(domain: str | None) -> str:
    return (domain or "").strip().lower().lstrip("@")


def _ensure_domain_free(domain: str, *, exclude_pk=None) -> None:
    if not domain:
        return
    taken = LdapConfig.objects.filter(email_domain__iexact=domain, is_active=True)
    if exclude_pk is not None:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise ValidationError(f"Another active directory already serves @{domain}.", code=9006)


def _role(role_uid):
    return rbac_selectors.get_role(role_uid) if role_uid else None


def create_config(
    *,
    name="",
    ldap_url,
    bind_dn,
    bind_password="",
    search_base="",
    email_attribute="mail",
    email_domain="",
    default_role_uid=None,
    is_active=True,
) -> LdapConfig:
    if not ldap_url or not bind_dn:
        raise ValidationError("ldap_url and bind_dn are required", code=9005)
    domain = _clean_domain(email_domain)
    if is_active:
        _ensure_domain_free(domain)
    config = LdapConfig(
        name=name or "",
        ldap_url=_clean_url(ldap_url),
        bind_dn=bind_dn,
        search_base=search_base or "",
        email_attribute=email_attribute or "mail",
        email_domain=domain,
        default_role=_role(default_role_uid),
        is_active=bool(is_active),
    )
    config.set_bind_password(bind_password)
    config.save()
    logger.info("[create_config] %s (%s)", config.name, config.ldap_url)
    return config


def update_config(
    *,
    uid,
    name=None,
    ldap_url=None,
    bind_dn=None,
    bind_password=None,
    search_base=None,
    email_attribute=None,
    email_domain=None,
    default_role_uid=None,
    clear_default_role=False,
    is_active=None,
) -> LdapConfig:
    config = get_config(uid)
    if ldap_url is not None:
        config.ldap_url = _clean_url(ldap_url)
    if name is not None:
        config.name = name
    if bind_dn is not None:
        config.bind_dn = bind_dn
    if bind_password:  # only rotate when a new value is supplied
        config.set_bind_password(bind_password)
    if search_base is not None:
        config.search_base = search_base
    if email_attribute is not None:
        config.email_attribute = email_attribute or "mail"
    if email_domain is not None:
        config.email_domain = _clean_domain(email_domain)
    if default_role_uid is not None:
        config.default_role = _role(default_role_uid)
    elif clear_default_role:
        config.default_role = None
    if is_active is not None:
        config.is_active = bool(is_active)
    if config.is_active:
        _ensure_domain_free(config.email_domain, exclude_pk=config.pk)
    config.save()
    return config


def delete_config(*, uid) -> LdapConfig:
    config = get_config(uid)
    domain = config.email_domain
    if domain:
        in_use = (
            get_user_model()
            .objects.filter(auth_provider=AuthProviderChoices.LDAP.value, email__iendswith=f"@{domain}")
            .exists()
        )
        if in_use:
            raise ValidationError(
                "People sign in through this directory. Switch it off instead of deleting it.", code=IN_USE
            )
    config.delete()  # soft delete
    return config
