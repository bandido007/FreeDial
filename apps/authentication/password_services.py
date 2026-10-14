"""Passwords: changing one, resetting a forgotten one, and the invitation that
lets the owner of a new account choose theirs.

Reset and invitation share one mechanism: a high-entropy, single-use token
travels in an e-mailed link; only its **SHA-256 hash** is stored (see
:class:`~apps.authentication.models.PasswordResetToken`), so a database leak
cannot be turned into working links. Following the link, the person sets a
password (validated by Django's configured validators); the token is consumed
and a confirmation is e-mailed.
"""

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone

from apps.authentication import events, tokens
from apps.authentication.enums import AuthEventChoices, TokenPurposeChoices
from apps.authentication.models import PasswordResetToken
from apps.authentication.notifications import ACCOUNT_INVITE, PASSWORD_CHANGED, PASSWORD_RESET
from apps.core.enums import AuthProviderChoices
from apps.core.exceptions import ValidationError
from apps.notifications import services as notification_services
from apps.notifications.enums import NotificationChannelChoices

logger = logging.getLogger(__name__)

RESET_TEMPLATE = "emails/password_reset.html"
CHANGED_TEMPLATE = "emails/password_changed.html"
INVITE_TEMPLATE = "emails/account_invite.html"

WEAK_PASSWORD = 9010
WRONG_OLD_PASSWORD = 9011
SAME_PASSWORD = 9016
INVALID_LINK = 9017
PASSWORD_REUSED = 9120
MANAGED_ELSEWHERE = 9123

# Deliberately generic: the same acknowledgement is returned whether or not an
# account exists, so the endpoint can't be used to enumerate registered e-mails.
REQUEST_ACK = (
    "If an account exists for that email, we've sent a link to reset your "
    "password. Please check your inbox (and your spam folder)."
)


def _reset_ttl_minutes() -> int:
    return int(getattr(settings, "PASSWORD_RESET_TTL_MINUTES", 30))


def _invite_ttl_hours() -> int:
    return int(getattr(settings, "INVITE_TTL_HOURS", 72))


def _link(token: str) -> str:
    domain = getattr(settings, "FRONTEND_DOMAIN", "").rstrip("/")
    path = getattr(settings, "PASSWORD_RESET_PATH", "/auth/set-password").strip("/")
    return f"{domain}/{path}/{token}"


def check_new_password(user, new_password: str) -> None:
    """Run the configured validators; translate a failure to the right code."""
    try:
        validate_password(new_password, user)
    except DjangoValidationError as exc:
        reused = any(getattr(error, "code", "") == "password_reused" for error in exc.error_list)
        raise ValidationError(
            " ".join(exc.messages), code=PASSWORD_REUSED if reused else WEAK_PASSWORD
        ) from exc


def _issue_token(user, *, purpose: str, lifetime: timedelta, ip: str | None = None) -> str:
    """Retire the account's unused tokens and mint a new one. Returns the raw token."""
    PasswordResetToken.objects.filter(user=user, is_used=False).update(is_used=True)
    raw_token = secrets.token_urlsafe(48)
    PasswordResetToken.objects.create(
        user=user,
        token_hash=PasswordResetToken.hash_token(raw_token),
        purpose=purpose,
        expires_at=timezone.now() + lifetime,
        requested_ip=ip or None,
    )
    return raw_token


def retire_tokens(user) -> int:
    return PasswordResetToken.objects.filter(user=user, is_used=False).update(is_used=True)


# ── Change (signed in) ───────────────────────────────────────────────────────
@transaction.atomic
def change_own_password(*, user, old_password: str, new_password: str, request=None):
    """Change the caller's own password (local accounts only).

    Directory and external accounts authenticate elsewhere, so this refuses
    rather than let a local hash drift out of sync with the source of truth.
    Every other session is signed out: whoever else knew the old password
    should not stay in.
    """
    if user.auth_provider != AuthProviderChoices.DB.value:
        raise ValidationError(
            "Your password is managed by your sign-in provider and can't be changed here.",
            code=MANAGED_ELSEWHERE,
        )
    if not old_password or not user.check_password(old_password):
        raise ValidationError("Invalid old password", code=WRONG_OLD_PASSWORD)
    if user.check_password(new_password):
        raise ValidationError(
            "New password must be different from your current password.", code=SAME_PASSWORD
        )
    check_new_password(user, new_password)

    user.set_password(new_password)
    user.must_change_password = False
    user.password_changed_at = timezone.now()
    user.save(update_fields=["password", "must_change_password", "password_changed_at"])

    tokens.revoke_all_sessions(user=user, keep_token=tokens.bearer_token(request) or None)
    events.record_authentication_event(
        event_type=AuthEventChoices.PasswordChanged, user=user, request=request
    )
    notify_password_changed(user)
    logger.info("[change_own_password] user %s", user.pk)
    return user


# ── Forgot password ──────────────────────────────────────────────────────────
def request_password_reset(*, email: str, ip: str | None = None, user_agent: str = "") -> dict:
    """Issue a reset link to an eligible local account (privacy-preserving).

    Only active, non-deleted **local (DB)** accounts get a link. For any
    ineligible or unknown e-mail this is a no-op that returns the same
    acknowledgement, so the caller can't tell the difference.
    """
    email = (email or "").strip().lower()
    if not email:
        raise ValidationError("Email is required", code=9005)

    user = get_user_model().objects.filter(email__iexact=email, is_active=True, deleted=False).first()
    eligible = user is not None and user.auth_provider == AuthProviderChoices.DB.value
    events.record_authentication_event(
        event_type=AuthEventChoices.PasswordResetRequested,
        user=user if eligible else None,
        email=email,
        ip_address=ip,
        user_agent=user_agent,
        metadata={"eligible": eligible},
    )
    if not eligible:
        return {"message": REQUEST_ACK, "email_ok": False}

    ok = send_reset_link(user, ip=ip)
    return {"message": REQUEST_ACK, "email_ok": ok}


def send_reset_link(user, *, ip: str | None = None) -> bool:
    raw_token = _issue_token(
        user,
        purpose=TokenPurposeChoices.RESET.value,
        lifetime=timedelta(minutes=_reset_ttl_minutes()),
        ip=ip,
    )
    url = _link(raw_token)
    # Local-development convenience: surface the link in the log so the flow
    # works without a mail server. Gated on DEBUG so a tokenised link can never
    # reach a production log.
    if settings.DEBUG:
        logger.warning("[DEV-ONLY] Password reset link for %s: %s", user.email, url)

    project = settings.PROJECT_NAME
    _, ok = notification_services.send_now(
        channel=NotificationChannelChoices.EMAIL.value,
        category=PASSWORD_RESET,
        subject=f"Reset your {project} password",
        message=f"Reset your {project} password using this link: {url}",
        to_email=user.email,
        receiver=user,
        html_template=RESET_TEMPLATE,
        variables={
            "full_name": user.get_full_name() or user.email,
            "reset_url": url,
            "ttl_minutes": _reset_ttl_minutes(),
            "project": project,
        },
        url=url,
        sensitive=True,  # scrub the tokenised link from the log after sending
    )
    logger.info("[send_reset_link] user %s email_ok=%s", user.pk, ok)
    return ok


def admin_send_password_reset(*, user, actor, request=None) -> bool:
    """An administrator sends someone a reset link. They never see or choose
    the password; the person's sessions end so the old one stops working."""
    if user.auth_provider != AuthProviderChoices.DB.value:
        raise ValidationError(
            "This account signs in through its provider; there is no password to reset here.",
            code=MANAGED_ELSEWHERE,
        )
    tokens.revoke_all_sessions(user=user)
    ok = send_reset_link(user)
    events.record_authentication_event(
        event_type=AuthEventChoices.PasswordResetRequested,
        user=user,
        request=request,
        metadata={"by": str(getattr(actor, "uid", "")), "email_ok": ok},
    )
    return ok


def require_password_change(*, user, actor=None):
    """Make the account choose a new password at its next sign-in."""
    user.must_change_password = True
    user.save(update_fields=["must_change_password"])
    return user


@transaction.atomic
def set_password_with_token(*, token: str, new_password: str, ip: str | None = None, user_agent: str = ""):
    """Validate a reset or invitation token and set the account's password.

    Raises ``ValidationError`` 9005 (missing), 9017 (invalid / expired / used
    token), 9010 (weak password), 9120 (reused) or 9016 (same as current). On
    success the token is consumed, the account is marked verified (following
    the link proves control of the address), every existing session ends, and a
    confirmation is e-mailed.
    """
    token = (token or "").strip()
    if not token or not new_password:
        raise ValidationError("Token and new password are required", code=9005)

    record = (
        PasswordResetToken.objects.filter(token_hash=PasswordResetToken.hash_token(token), is_used=False)
        .select_related("user")
        .first()
    )
    if record is None or record.is_expired:
        raise ValidationError(
            "This link is invalid, has expired or was already used. Please request a new one.",
            code=INVALID_LINK,
        )

    user = record.user
    if getattr(user, "deleted", False) or not user.is_active:
        raise ValidationError(
            "This link is invalid, has expired or was already used. Please request a new one.",
            code=INVALID_LINK,
        )
    if user.has_usable_password() and user.check_password(new_password):
        raise ValidationError(
            "New password must be different from your current password.", code=SAME_PASSWORD
        )
    check_new_password(user, new_password)

    invited = record.purpose == TokenPurposeChoices.INVITE.value
    user.set_password(new_password)
    user.must_change_password = False
    user.password_changed_at = timezone.now()
    user.is_verified = True
    user.save(update_fields=["password", "must_change_password", "password_changed_at", "is_verified"])

    record.is_used = True
    record.save(update_fields=["is_used"])
    tokens.revoke_all_sessions(user=user)

    events.record_authentication_event(
        event_type=AuthEventChoices.PasswordResetCompleted,
        user=user,
        ip_address=ip,
        user_agent=user_agent,
        metadata={"invitation": invited},
    )
    if invited:
        _welcome(user)
    else:
        notify_password_changed(user)
    logger.info("[set_password_with_token] user %s (%s)", user.pk, record.purpose)
    return user


def _welcome(user) -> None:
    try:
        from apps.users import account_notices

        account_notices.notify_welcome(user)
    except Exception:  # noqa: BLE001
        logger.exception("[password_services] welcome notice failed for user %s", user.pk)


# ── Invitation ───────────────────────────────────────────────────────────────
def send_invitation(user, *, invited_by=None) -> bool:
    """Mail the owner of a new account the link to choose their password."""
    raw_token = _issue_token(
        user,
        purpose=TokenPurposeChoices.INVITE.value,
        lifetime=timedelta(hours=_invite_ttl_hours()),
    )
    url = _link(raw_token)
    if settings.DEBUG:
        logger.warning("[DEV-ONLY] Invitation link for %s: %s", user.email, url)

    project = settings.PROJECT_NAME
    inviter = invited_by.get_full_name() if invited_by is not None else ""
    _, ok = notification_services.send_now(
        channel=NotificationChannelChoices.EMAIL.value,
        category=ACCOUNT_INVITE,
        subject=f"Your {project} account is ready",
        message=(
            f"An account has been created for you on {project}. "
            f"Set your password to activate it: {url} (the link is valid for {_invite_ttl_hours()} hours)."
        ),
        to_email=user.email,
        receiver=user,
        html_template=INVITE_TEMPLATE,
        variables={
            "full_name": user.get_full_name() or user.email,
            "invite_url": url,
            "ttl_hours": _invite_ttl_hours(),
            "invited_by": inviter,
            "project": project,
        },
        url=url,
        sensitive=True,
    )
    events.record_authentication_event(
        event_type=AuthEventChoices.Invited,
        user=user,
        metadata={"by": str(getattr(invited_by, "uid", "")), "email_ok": ok},
    )
    logger.info("[send_invitation] user %s email_ok=%s", user.pk, ok)
    return ok


# ── Notices ──────────────────────────────────────────────────────────────────
def notify_password_changed(user) -> bool:
    """E-mail a "your password was changed" confirmation. Best-effort.

    Shared by the change and the reset flows so every password change produces
    the same security notice.
    """
    if not user.email:
        return False
    project = settings.PROJECT_NAME
    try:
        _, ok = notification_services.send_now(
            channel=NotificationChannelChoices.EMAIL.value,
            category=PASSWORD_CHANGED,
            subject=f"Your {project} password was changed",
            message=(
                f"Your {project} account password was just changed. If this wasn't you, reset it immediately."
            ),
            to_email=user.email,
            receiver=user,
            html_template=CHANGED_TEMPLATE,
            variables={
                "full_name": user.get_full_name() or user.email,
                "changed_at": timezone.localtime().strftime("%d %b %Y, %H:%M %Z"),
                "project": project,
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("[notify_password_changed] failed for user %s", user.pk)
        return False
    return ok


def purge_spent_tokens() -> int:
    """Remove links that were used or expired more than a week ago."""
    from django.db.models import Q

    horizon = timezone.now() - timedelta(days=7)
    spent = PasswordResetToken.all_objects.filter(Q(is_used=True) | Q(expires_at__lt=timezone.now())).filter(
        updated_at__lt=horizon
    )
    count = spent.count()
    spent.delete()
    return count
