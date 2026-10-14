"""Two-factor authentication (authenticator app + recovery codes).

Enrolment is two steps: :func:`begin_enrollment` creates the secret and returns
what the authenticator app needs; :func:`confirm_enrollment` proves the app
works by checking the first code, switches two-factor on, and hands out the
recovery codes — **once**; only their hashes are kept.

Sign-in checks go through :func:`code_ok`, which accepts a current code or an
unused recovery code, and never the same code twice.
"""

import hashlib
import logging
import secrets

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.authentication import events, totp
from apps.authentication.enums import STATUS_FAILURE, AuthEventChoices
from apps.authentication.gates import mfa_required_for
from apps.authentication.models import MfaCredential, MfaRecoveryCode
from apps.authentication.notifications import MFA_CHANGED
from apps.core.exceptions import ValidationError
from apps.notifications import routes
from apps.notifications import services as notification_services
from apps.notifications.enums import NotificationChannelChoices

logger = logging.getLogger(__name__)

RECOVERY_CODE_COUNT = 10
INVALID_CODE = 9013
NOT_ENABLED = 9121
REQUIRED_BY_POLICY = 9122
CODE_REUSED = 9124

# A verified identity parked behind a short-lived ticket while the second
# factor is asked for (external sign-in: the provider's credential is
# single-use, so it cannot simply be posted again with the code).
TICKET_TTL_SECONDS = 300
TICKET_MAX_ATTEMPTS = 5


# ── Recovery codes ───────────────────────────────────────────────────────────
def _generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """High-entropy single-use codes, formatted ``xxxxx-xxxxx`` for readability."""

    def one() -> str:
        raw = secrets.token_hex(5)  # 10 hex characters = 40 bits
        return f"{raw[:5]}-{raw[5:]}"

    return [one() for _ in range(count)]


def _hash_recovery(code: str) -> str:
    """Hash a recovery code. High-entropy tokens don't need slow hashing; a
    keyed SHA-256 (peppered with SECRET_KEY) is sufficient and avoids a
    per-sign-in cost."""
    normalized = (code or "").strip().lower().replace("-", "").replace(" ", "")
    return hashlib.sha256(f"{settings.SECRET_KEY}:{normalized}".encode()).hexdigest()


def _replace_recovery_codes(user) -> list[str]:
    codes = _generate_recovery_codes()
    MfaRecoveryCode.objects.filter(user=user).delete()
    MfaRecoveryCode.objects.bulk_create(
        [MfaRecoveryCode(user=user, code_hash=_hash_recovery(code)) for code in codes]
    )
    return codes


def recovery_codes_remaining(user) -> int:
    return MfaRecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


def consume_recovery_code(*, user, code: str) -> bool:
    """Spend a single-use recovery code (returns True if it matched)."""
    if not code:
        return False
    # The conditional UPDATE is the lock: two requests racing with the same
    # code cannot both win.
    spent = MfaRecoveryCode.objects.filter(
        user=user, code_hash=_hash_recovery(code), used_at__isnull=True
    ).update(used_at=timezone.now())
    if not spent:
        return False
    left = recovery_codes_remaining(user)
    events.record_authentication_event(
        event_type=AuthEventChoices.MfaRecoveryCodeUsed, user=user, metadata={"remaining": left}
    )
    logger.info("[consume_recovery_code] user %s used a recovery code (%s left)", user.pk, left)
    return True


# ── Checking a code ──────────────────────────────────────────────────────────
def _credential(user) -> MfaCredential | None:
    return MfaCredential.objects.filter(user=user).first()


def _accept_totp(credential: MfaCredential, code: str) -> bool:
    """Check an authenticator code and burn its time step so it cannot be replayed."""
    step = totp.match(credential.secret, code)
    if step is None:
        return False
    # Conditional UPDATE: succeeds only if this step is newer than the last one
    # accepted — atomically, so the same code cannot pass twice even in a race.
    accepted = MfaCredential.objects.filter(pk=credential.pk, last_used_step__lt=step).update(
        last_used_step=step, last_used_at=timezone.now()
    )
    return bool(accepted)


def code_ok(user, code: str) -> bool:
    """A current authenticator code, or an unspent recovery code (single-use)."""
    code = (code or "").strip()
    if not code:
        return False
    credential = _credential(user)
    if credential is None or not credential.is_confirmed:
        return False
    return _accept_totp(credential, code) or consume_recovery_code(user=user, code=code)


def _require_code(user, code: str, *, request=None) -> None:
    if not code_ok(user, code):
        events.record_authentication_event(
            event_type=AuthEventChoices.MfaFailed, status=STATUS_FAILURE, user=user, request=request
        )
        raise ValidationError("That code is not valid. Please try again.", code=INVALID_CODE)


# ── Enrolment ────────────────────────────────────────────────────────────────
def begin_enrollment(*, user) -> dict:
    """Generate + store a secret (not yet active). Returns secret + otpauth URI."""
    if user.mfa_enabled:
        raise ValidationError(
            "Two-factor authentication is already on. Turn it off first to set it up again.", code=9002
        )
    secret = totp.generate_secret()
    credential, _ = MfaCredential.all_objects.get_or_create(user=user)
    credential.secret = secret
    credential.confirmed_at = None
    credential.last_used_step = 0
    credential.deleted = False
    credential.save()
    logger.info("[begin_enrollment] user %s", user.pk)
    return {
        "secret": secret,
        "otpauth_uri": totp.provisioning_uri(secret, account=user.email, issuer=settings.PROJECT_NAME),
    }


@transaction.atomic
def confirm_enrollment(*, user, code: str, request=None) -> list[str]:
    """Verify the first code, switch two-factor on, and return the recovery codes.

    The plaintext codes are returned **once** (only their hashes are stored) so
    the client can show them for the person to save — their way back in if the
    authenticator is lost.
    """
    credential = _credential(user)
    if credential is None or not credential.secret:
        raise ValidationError("Start the two-factor setup first.", code=9002)
    if not _accept_totp(credential, code):
        raise ValidationError("That code is not valid. Please try again.", code=INVALID_CODE)
    credential.confirmed_at = timezone.now()
    credential.save(update_fields=["confirmed_at"])
    recovery = _replace_recovery_codes(user)
    user.mfa_enabled = True
    user.save(update_fields=["mfa_enabled"])
    events.record_authentication_event(event_type=AuthEventChoices.MfaEnabled, user=user, request=request)
    _notify_changed(user, "turned on")
    logger.info("[confirm_enrollment] user %s enabled two-factor", user.pk)
    return recovery


def regenerate_recovery_codes(*, user, code: str, request=None) -> list[str]:
    """Replace the recovery codes with a fresh set (invalidates the old ones)."""
    if not user.mfa_enabled:
        raise ValidationError("Two-factor authentication is not turned on.", code=NOT_ENABLED)
    _require_code(user, code, request=request)
    recovery = _replace_recovery_codes(user)
    logger.info("[regenerate_recovery_codes] user %s", user.pk)
    return recovery


def _clear(user) -> None:
    MfaCredential.all_objects.filter(user=user).delete()
    MfaRecoveryCode.objects.filter(user=user).delete()
    user.mfa_enabled = False
    user.save(update_fields=["mfa_enabled"])


@transaction.atomic
def disable(*, user, code: str, request=None):
    """Turn two-factor off after proving possession with a current code (or a
    recovery code). Refused when policy requires it for this account."""
    if not user.mfa_enabled:
        raise ValidationError("Two-factor authentication is not turned on.", code=NOT_ENABLED)
    if mfa_required_for(user):
        raise ValidationError(
            "Two-factor authentication is required for your account and cannot be turned off.",
            code=REQUIRED_BY_POLICY,
        )
    _require_code(user, code, request=request)
    _clear(user)
    events.record_authentication_event(event_type=AuthEventChoices.MfaDisabled, user=user, request=request)
    _notify_changed(user, "turned off")
    logger.info("[disable] user %s disabled two-factor", user.pk)
    return user


@transaction.atomic
def admin_reset(*, user, actor, request=None):
    """Operator backstop: clear a person's two-factor when they are locked out
    (lost device, no recovery codes). Signs them out everywhere."""
    from apps.authentication import tokens

    _clear(user)
    tokens.revoke_all_sessions(user=user)
    events.record_authentication_event(
        event_type=AuthEventChoices.MfaReset,
        user=user,
        request=request,
        metadata={"by": str(getattr(actor, "uid", ""))},
    )
    _notify_changed(user, "reset by an administrator")
    logger.info("[admin_reset] user %s reset by %s", user.pk, getattr(actor, "pk", None))
    return user


def _notify_changed(user, what: str) -> None:
    project = settings.PROJECT_NAME
    try:
        notification_services.queue(
            channel=NotificationChannelChoices.EMAIL.value,
            category=MFA_CHANGED,
            subject=f"Two-factor authentication was {what}",
            message=(
                f"Two-factor authentication on your {project} account was {what}. "
                "If this was not you, change your password and contact the administrator."
            ),
            receiver=user,
            variables={"full_name": user.get_full_name() or user.email, "project": project},
            url=routes.MY_SECURITY,
        )
    except Exception:  # noqa: BLE001 — a notice must never fail the change itself
        logger.exception("[mfa] could not queue the change notice for user %s", user.pk)


# ── Tickets (second leg of an external sign-in) ──────────────────────────────
def _ticket_key(ticket: str) -> str:
    return f"mfa-ticket:{hashlib.sha256(ticket.encode()).hexdigest()}"


def issue_ticket(user, *, provider: str = "") -> str:
    ticket = secrets.token_urlsafe(32)
    cache.set(
        _ticket_key(ticket),
        {"user_id": user.pk, "attempts": 0, "provider": provider},
        timeout=TICKET_TTL_SECONDS,
    )
    return ticket


def resolve_ticket(ticket: str) -> tuple[object | None, str]:
    """``(user, provider)`` a live ticket stands for; ``(None, "")`` when unknown."""
    from django.contrib.auth import get_user_model

    entry = cache.get(_ticket_key(ticket)) if ticket else None
    if not entry:
        return None, ""
    user = get_user_model().objects.filter(pk=entry["user_id"]).first()
    return user, entry.get("provider", "")


def record_ticket_attempt(ticket: str) -> bool:
    """Count a failed code; returns ``False`` (and burns the ticket) once the
    attempt budget is spent, so a ticket cannot be brute-forced."""
    key = _ticket_key(ticket)
    entry = cache.get(key)
    if not entry:
        return False
    entry["attempts"] = int(entry.get("attempts", 0)) + 1
    if entry["attempts"] >= TICKET_MAX_ATTEMPTS:
        cache.delete(key)
        return False
    cache.set(key, entry, timeout=TICKET_TTL_SECONDS)
    return True


def consume_ticket(ticket: str) -> None:
    cache.delete(_ticket_key(ticket))
