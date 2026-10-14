"""One-time verification codes (OTP): issue, deliver (email + SMS) and validate.

A 6-digit code proves control of a contact channel. It is generated from the
operating system's secure random source, stored only as a salted hash
(``make_password`` — PBKDF2 + per-row salt), and delivered through the
notifications subsystem.

The core is **purpose-parametric** (``purpose`` on :class:`OtpVerification`), so
the same machinery serves registration, email-change re-verification, phone
verification, account deletion — and any purpose a business app invents::

    outcome = otp_services.issue_otp(user, purpose="approve_payment")
    ...
    otp_services.validate_otp(user, code, purpose="approve_payment")

Delivery is **synchronous** on purpose so the caller can tell the person which
channel(s) actually received the code.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.utils import timezone

from apps.core.exceptions import ThrottledError, ValidationError
from apps.core.ratelimit import throttle
from apps.core.utils import Utils
from apps.notifications import services as notification_services
from apps.notifications.enums import NotificationChannelChoices
from apps.users.models import OtpVerification, User
from apps.users.notifications import OTP_VERIFICATION

logger = logging.getLogger(__name__)

# OTP purposes (the ``purpose`` column) — one live code per (user, purpose).
PURPOSE_REGISTRATION = "registration"
PURPOSE_EMAIL_CHANGE = "email_change"
PURPOSE_PHONE_VERIFY = "phone_verify"
PURPOSE_ACCOUNT_DELETE = "account_delete"

# What the e-mail says the code is for ("Use the code below to …").
PURPOSE_LABELS = {
    PURPOSE_REGISTRATION: "confirm your account",
    PURPOSE_EMAIL_CHANGE: "confirm your new e-mail address",
    PURPOSE_PHONE_VERIFY: "verify your phone number",
    PURPOSE_ACCOUNT_DELETE: "confirm the deletion of your account",
}

OTP_TEMPLATE = "emails/otp_verification.html"
INVALID_CODE = 9013
EXPIRED_CODE = 9014


def _ttl_minutes() -> int:
    return int(getattr(settings, "OTP_TTL_MINUTES", 10))


def _max_attempts() -> int:
    return int(getattr(settings, "OTP_MAX_ATTEMPTS", 5))


def _smart_message(*, email_attempted, email_ok, sms_attempted, sms_ok) -> str:
    """The user-facing message, branched on which channel(s) delivered."""
    if email_ok and sms_ok:
        return (
            "We've sent a 6-digit verification code to both your email and your "
            "phone (SMS). Enter it to verify."
        )
    if email_ok and not sms_ok:
        tail = " (We couldn't reach your phone by SMS.)" if sms_attempted else ""
        return f"We've sent a 6-digit verification code to your email.{tail}"
    if sms_ok and not email_ok:
        tail = " (We couldn't reach your email.)" if email_attempted else ""
        return f"We've sent a 6-digit verification code to your phone by SMS.{tail}"
    return (
        "We couldn't deliver your verification code right now — the messaging "
        "service is unavailable. Please request a new code shortly."
    )


# ── Generic core ────────────────────────────────────────────────────────────
def issue_otp(
    user: User,
    *,
    purpose: str,
    to_email: str | None = None,
    to_phone: str | None = None,
    subject: str | None = None,
) -> dict:
    """Generate, store and deliver a one-time code for ``purpose``.

    ``to_email`` / ``to_phone`` default to the user's current contacts; pass an
    override to send elsewhere (e.g. the *new* email during an email change),
    or ``""`` to skip that channel. Returns ``{"message", "email_ok", "sms_ok"}``.
    """
    to_email = to_email if to_email is not None else user.email
    to_phone = to_phone if to_phone is not None else user.phone
    project = settings.PROJECT_NAME
    subject = subject or f"Your {project} verification code"

    # One live code per (user, purpose): retire any earlier unused ones.
    OtpVerification.objects.filter(user=user, purpose=purpose, is_used=False).update(is_used=True)

    code = Utils.generate_otp()
    if settings.DEBUG:
        logger.warning(
            "[DEV-ONLY] %s code for %s is %s (valid %s min).", purpose, user.email, code, _ttl_minutes()
        )

    otp = OtpVerification.objects.create(
        user=user,
        code_hash=make_password(code),
        purpose=purpose,
        expires_at=timezone.now() + timedelta(minutes=_ttl_minutes()),
    )

    variables = {
        "full_name": user.get_full_name() or user.email,
        "otp": code,
        "ttl_minutes": _ttl_minutes(),
        "purpose_label": PURPOSE_LABELS.get(purpose, "continue"),
        "project": project,
    }
    sms_text = (
        f"{project}: your verification code is {code}. "
        f"It expires in {_ttl_minutes()} minutes. Do not share it."
    )
    email_attempted, sms_attempted = bool(to_email), bool(to_phone)
    email_ok = sms_ok = False

    if email_attempted:
        _, email_ok = notification_services.send_now(
            channel=NotificationChannelChoices.EMAIL.value,
            category=OTP_VERIFICATION,
            subject=subject,
            message=sms_text,
            to_email=to_email,
            receiver=user,
            html_template=OTP_TEMPLATE,
            variables=variables,
            sensitive=True,
        )
    if sms_attempted:
        _, sms_ok = notification_services.send_now(
            channel=NotificationChannelChoices.SMS.value,
            category=OTP_VERIFICATION,
            message=sms_text,
            to_phone=to_phone,
            receiver=user,
            variables=variables,
            sensitive=True,
        )

    otp.channels_sent = {"email": email_ok, "sms": sms_ok}
    otp.save(update_fields=["channels_sent"])
    logger.info("[issue_otp:%s] user %s email_ok=%s sms_ok=%s", purpose, user.pk, email_ok, sms_ok)
    return {
        "message": _smart_message(
            email_attempted=email_attempted,
            email_ok=email_ok,
            sms_attempted=sms_attempted,
            sms_ok=sms_ok,
        ),
        "email_ok": email_ok,
        "sms_ok": sms_ok,
    }


def _register_attempt(otp: OtpVerification) -> int:
    """Count one guess against ``otp`` and return the running total.

    The count lives in the **cache**, not only on the row. A wrong code raises,
    the API layer rolls the transaction back, and a counter kept in the
    database would be rolled back with it — every guess would be the first.
    The cache is not transactional, so the limit actually holds.
    """
    key = f"otp-attempts:{otp.uid}"
    lifetime = max(60, int((otp.expires_at - timezone.now()).total_seconds()) + 60)
    try:
        cache.add(key, 0, timeout=lifetime)
        return int(cache.incr(key))
    except ValueError:
        cache.set(key, 1, timeout=lifetime)
        return 1


def _consume_otp(otp: OtpVerification, code: str) -> None:
    """Validate ``code`` against ``otp`` and mark it used; raise on failure."""
    if otp.is_expired:
        otp.is_used = True
        otp.save(update_fields=["is_used"])
        raise ValidationError("The verification code has expired", code=EXPIRED_CODE)

    attempts = _register_attempt(otp)
    otp.attempts = max(otp.attempts + 1, attempts)
    if attempts > _max_attempts():
        otp.is_used = True
        otp.save(update_fields=["attempts", "is_used"])
        raise ValidationError("Too many attempts. Please request a new code.", code=INVALID_CODE)

    if not check_password(code, otp.code_hash):
        otp.save(update_fields=["attempts"])
        raise ValidationError("Invalid or incorrect verification code", code=INVALID_CODE)

    otp.is_used = True
    otp.save(update_fields=["attempts", "is_used"])


def validate_otp(user: User, code: str, *, purpose: str) -> OtpVerification:
    """Validate the latest active ``purpose`` code for ``user``."""
    code = (code or "").strip()
    if not code:
        raise ValidationError("The verification code is required", code=9005)
    otp = (
        OtpVerification.objects.filter(user=user, purpose=purpose, is_used=False)
        .order_by("-created_at")
        .first()
    )
    if otp is None:
        raise ValidationError("Invalid or incorrect verification code", code=INVALID_CODE)
    _consume_otp(otp, code)
    return otp


# ── Registration (public, email-keyed) ──────────────────────────────────────
def issue_registration_otp(user: User) -> dict:
    """Issue + deliver a registration code over email + SMS."""
    return issue_otp(
        user, purpose=PURPOSE_REGISTRATION, subject=f"Verify your {settings.PROJECT_NAME} account"
    )


def validate_registration_otp(*, email: str, code: str) -> User:
    """Validate a registration code for ``email``; flip ``is_verified`` (+phone).

    Errors are deliberately generic to avoid revealing whether the email exists.
    """
    from apps.users import account_notices

    email = (email or "").strip().lower()
    code = (code or "").strip()
    if not email or not code:
        raise ValidationError("Email and code are required", code=9005)

    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        raise ValidationError("Invalid or incorrect verification code", code=INVALID_CODE)

    otp = validate_otp(user, code, purpose=PURPOSE_REGISTRATION)

    verified_fields = []
    if not user.is_verified:
        user.is_verified = True
        verified_fields.append("is_verified")
    if (otp.channels_sent or {}).get("sms") and not user.phone_verified:
        user.phone_verified = True
        verified_fields.append("phone_verified")
    if verified_fields:
        user.save(update_fields=verified_fields)
        # The account just became usable — say so. Only on the transition, so
        # a re-entered code never re-sends the welcome.
        if "is_verified" in verified_fields:
            account_notices.notify_welcome(user)
    logger.info("[validate_registration_otp] user %s verified", user.pk)
    return user


def enforce_resend_limits(email: str) -> None:
    """Cooldown + daily cap on resends, keyed on the raw email (no enumeration leak)."""
    cooldown = int(getattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 45))
    cooldown_key = f"otp-cooldown:{email}"
    if cache.get(cooldown_key):
        raise ThrottledError("Please wait a moment before requesting another code.", code=9018)
    throttle(
        "otp-daily",
        email,
        limit=int(getattr(settings, "OTP_MAX_PER_DAY", 8)),
        window=86_400,
        message="You've requested too many codes today. Please try again tomorrow.",
    )
    cache.set(cooldown_key, 1, timeout=cooldown)


def resend_registration_otp(email: str) -> dict:
    """Re-issue a registration code. Privacy-preserving for unknown emails."""
    email = (email or "").strip().lower()
    if not email:
        raise ValidationError("Email is required", code=9005)

    enforce_resend_limits(email)

    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        return {
            "message": "If an account exists for that email, a new code has been sent.",
            "email_ok": False,
            "sms_ok": False,
        }
    if user.is_verified:
        return {"message": "Your account is already verified.", "email_ok": False, "sms_ok": False}
    return issue_registration_otp(user)


def purge_old_codes() -> int:
    """Delete used and expired code rows past ``OTP_RETENTION_DAYS``."""
    from django.db.models import Q

    now = timezone.now()
    retention = int(getattr(settings, "OTP_RETENTION_DAYS", 3))
    stale = OtpVerification.all_objects.filter(updated_at__lt=now - timedelta(days=retention)).filter(
        Q(is_used=True) | Q(expires_at__lt=now)
    )
    count = stale.count()
    stale.delete()
    return count
