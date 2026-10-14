"""How an account comes to exist.

* :func:`register_local_user` — a person signs themselves up (when
  ``ALLOW_SELF_REGISTRATION`` is on).
* :func:`create_user` — an administrator creates the account; the person
  receives an invitation to set their password.

External sign-in and directory accounts are provisioned by
``apps.authentication`` on first sign-in.
"""

import logging

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.enums import AccountTypeChoices, AuthProviderChoices, GenderChoices, to_values
from apps.core.exceptions import DuplicateError, PermissionDeniedError, ValidationError
from apps.core.hooks import policy
from apps.core.validators import (
    normalize_phone,
    validate_date_of_birth,
    validate_email_address,
    validate_person_name,
)
from apps.users import consent_services, selectors, signals
from apps.users.models import User

logger = logging.getLogger(__name__)

REGISTRATION_CLOSED = 9100
EMAIL_EXISTS = 9101
PHONE_EXISTS = 9020
CONSENT_REQUIRED = 9023
NO_PENDING_INVITATION = 9103


def self_registration_open() -> bool:
    return bool(policy("ALLOW_SELF_REGISTRATION", False))


def ensure_email_free(email: str, *, exclude_pk=None) -> None:
    taken = User.all_objects.filter(email__iexact=email)
    if exclude_pk is not None:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise DuplicateError("An account with this email already exists", code=EMAIL_EXISTS)


def ensure_phone_free(phone: str, *, exclude_pk=None) -> None:
    """A phone number identifies one person."""
    if not phone:
        return
    taken = User.all_objects.filter(phone=phone)
    if exclude_pk is not None:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise DuplicateError("An account with this phone number already exists", code=PHONE_EXISTS)


# ── Self-registration ────────────────────────────────────────────────────────
def register_local_user(
    *,
    email: str,
    password: str,
    first_name: str = "",
    middle_name: str = "",
    last_name: str = "",
    phone: str = "",
    date_of_birth=None,
    accepted_terms: bool = False,
    ip: str | None = None,
    user_agent: str = "",
) -> User:
    """Create a self-registered External account.

    Enforces the onboarding contract up front: a valid non-disposable email, a
    real name, a well-formed and not-already-used phone, a strong password, and
    accepted legal consent. Consent is recorded (auditably) in the same
    transaction, so an account never exists without its lawful-basis record.
    """
    if not self_registration_open():
        raise PermissionDeniedError(
            "Creating your own account is not available. Ask an administrator for an account.",
            code=REGISTRATION_CLOSED,
        )
    email = validate_email_address(email)  # format + disposable check, lower-cased
    if not password:
        raise ValidationError("A password is required", code=9005)
    # Names come back normalised (collapsed whitespace) and letter-shaped only.
    first_name = validate_person_name(first_name, label="First name")
    last_name = validate_person_name(last_name, label="Last name")
    middle_name = validate_person_name(middle_name, label="Middle name", required=False)
    if date_of_birth is not None:
        validate_date_of_birth(date_of_birth)
    if not accepted_terms:
        raise ValidationError(
            "You must accept the Terms of Service and Privacy Policy to register.",
            code=CONSENT_REQUIRED,
        )
    ensure_email_free(email)

    phone = normalize_phone(phone)  # required + E.164; raises 9021 if malformed
    ensure_phone_free(phone)

    user = User(
        email=email,
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
        phone=phone,
        date_of_birth=date_of_birth,
        account_type=AccountTypeChoices.External.value,
        auth_provider=AuthProviderChoices.DB.value,
        is_verified=False,
    )
    try:
        validate_password(password, user)
    except DjangoValidationError as exc:
        raise ValidationError(" ".join(exc.messages), code=9010) from exc

    with transaction.atomic():
        user.set_password(password)
        user.password_changed_at = timezone.now()
        user.save()
        consent_services.accept_registration_consents(user=user, ip=ip, user_agent=user_agent)
    signals.announce(signals.user_registered, user=user, ip_address=ip, user_agent=user_agent)
    logger.info("[register_local_user] user %s registered", user.pk)
    return user


# ── Accounts created by an administrator ─────────────────────────────────────
def create_user(
    *,
    email: str,
    first_name: str,
    last_name: str,
    middle_name: str = "",
    phone: str = "",
    gender: str = "",
    date_of_birth=None,
    account_type: str = AccountTypeChoices.Internal.value,
    role_uids: list | None = None,
    actor=None,
    send_invitation: bool = True,
) -> tuple[User, bool]:
    """Create an account on someone's behalf and invite them to activate it.

    The account has **no password**: nobody but its owner ever knows one. The
    invitation carries a single-use link to set it, which also proves the
    address, so the account becomes verified the moment it is activated.

    Returns ``(user, invitation_sent)``.
    """
    from apps.rbac import services as rbac_services

    email = validate_email_address(email, allow_disposable=False)
    first_name = validate_person_name(first_name, label="First name")
    last_name = validate_person_name(last_name, label="Last name")
    middle_name = validate_person_name(middle_name, label="Middle name", required=False)
    account_type = to_values(account_type) or AccountTypeChoices.Internal.value
    if account_type not in AccountTypeChoices.values():
        raise ValidationError("Invalid account type", code=9002)
    gender = to_values(gender) or ""
    if gender and gender not in GenderChoices.values():
        raise ValidationError("Invalid gender", code=9002)
    if date_of_birth is not None:
        validate_date_of_birth(date_of_birth)
    ensure_email_free(email)
    if phone:
        phone = normalize_phone(phone)
        ensure_phone_free(phone)

    with transaction.atomic():
        user = User(
            email=email,
            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,
            phone=phone or "",
            gender=gender,
            date_of_birth=date_of_birth,
            account_type=account_type,
            auth_provider=AuthProviderChoices.DB.value,
            is_verified=False,
            is_active=True,
        )
        user.set_unusable_password()
        user.save()
        if role_uids:
            rbac_services.set_user_roles(user=user, role_uids=role_uids, actor=actor)

    sent = send_invitation_to(user, invited_by=actor) if send_invitation else False
    logger.info("[create_user] user %s created by %s", user.pk, getattr(actor, "pk", None))
    return user, sent


def send_invitation_to(user: User, *, invited_by=None) -> bool:
    """Ask the apps above to issue and mail the set-password link."""
    user.invited_at = timezone.now()
    user.save(update_fields=["invited_at"])
    answers = signals.announce(signals.user_invited, user=user, invited_by=invited_by)
    return any(isinstance(answer, dict) and answer.get("email_ok") for answer in answers)


def resend_invitation(*, uid, actor=None) -> tuple[User, bool]:
    user = selectors.get_user(uid)
    if not user.invitation_pending:
        raise ValidationError(
            "This account is already activated; there is no invitation to send again.",
            code=NO_PENDING_INVITATION,
        )
    return user, send_invitation_to(user, invited_by=actor)


def cancel_invitation(*, uid, actor=None) -> User:
    """Withdraw an invitation nobody accepted: the account is removed for good,
    which frees its e-mail address and phone number."""
    user = selectors.get_user(uid)
    if not user.invitation_pending or user.last_login is not None:
        raise ValidationError(
            "This account is already activated. Deactivate or delete it instead.",
            code=NO_PENDING_INVITATION,
        )
    signals.announce(signals.account_gated, user=user, reason=signals.GATE_DELETED)
    snapshot = User(
        pk=user.pk, uid=user.uid, email=user.email, first_name=user.first_name, last_name=user.last_name
    )
    user.hard_delete()
    logger.info("[cancel_invitation] user %s removed by %s", snapshot.pk, getattr(actor, "pk", None))
    return snapshot
