"""Write-side business logic for users.

Services own all mutations and side effects. They validate, enforce invariants,
and raise :class:`~apps.core.exceptions.ApplicationError` subclasses on failure
(the GraphQL ``log_errors_and_warnings`` decorator turns those into the standard
error envelope). Keeping writes here means mutations stay thin and the same
logic is reusable from REST endpoints, management commands or Celery tasks.

Three groups live here:
  * **Self-service** — profile, e-mail change, phone verification, data rights.
  * **Administration** — edit, activate, suspend, delete, restore, assign roles.
  * **Housekeeping** — purging sign-ups that were never verified.

Account creation is in :mod:`apps.users.registration_services`; passwords,
two-factor and sessions are in :mod:`apps.authentication`.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.core.enums import (
    AccountStatusChoices,
    AccountTypeChoices,
    AuthProviderChoices,
    GenderChoices,
    to_values,
)
from apps.core.exceptions import ValidationError
from apps.core.validators import (
    normalize_phone,
    validate_date_of_birth,
    validate_email_address,
    validate_person_name,
)
from apps.users import account_notices, data_export, otp_services, selectors, signals
from apps.users.models import User
from apps.users.registration_services import ensure_email_free, ensure_phone_free

logger = logging.getLogger(__name__)

OWN_ACCOUNT = 9102
MFA_NEEDED_FOR_EMAIL_CHANGE = 9104

# Profile fields a person may edit on themselves. Listed once so the
# self-service and the administrator's edit share one allow-list.
SELF_EDITABLE_PROFILE_FIELDS = (
    "first_name",
    "middle_name",
    "last_name",
    "phone",
    "gender",
    "photo",
    "date_of_birth",
)


# ── Profile ──────────────────────────────────────────────────────────────────
def _apply_profile(user: User, *, first_name, middle_name, last_name, phone, gender, photo, date_of_birth):
    """Validate and set the shared profile fields (PATCH: ``None`` = keep)."""
    gender = to_values(gender)
    if gender is not None and gender != "" and gender not in GenderChoices.values():
        raise ValidationError("Invalid gender", code=9002)

    # Same name contract as registration (shared validator, so they can't drift).
    # First/last stay required — a PATCH may change them but never blank them.
    if first_name is not None:
        first_name = validate_person_name(first_name, label="First name")
    if middle_name is not None:
        middle_name = validate_person_name(middle_name, label="Middle name", required=False)
    if last_name is not None:
        last_name = validate_person_name(last_name, label="Last name")

    # Phone changes are re-validated and re-checked for uniqueness; changing it
    # invalidates the previous SMS proof.
    if phone is not None and phone != "":
        phone = normalize_phone(phone)
        ensure_phone_free(phone, exclude_pk=user.pk)
    if phone is not None and phone != user.phone:
        user.phone_verified = False

    if date_of_birth is not None and date_of_birth != "":
        validate_date_of_birth(date_of_birth)

    updates = {
        "first_name": first_name,
        "middle_name": middle_name,
        "last_name": last_name,
        "phone": phone,
        "gender": gender,
        "photo": photo,
        "date_of_birth": date_of_birth,
    }
    for field, value in updates.items():
        if value is not None:
            setattr(user, field, value.strip() if isinstance(value, str) else value)


def update_own_profile(
    *,
    user: User,
    first_name=None,
    middle_name=None,
    last_name=None,
    phone=None,
    gender=None,
    photo=None,
    date_of_birth=None,
) -> User:
    """Let a signed-in person edit their own basic details.

    Identity-*defining* fields (email, account type, roles) are outside the
    self-service surface. Every argument is optional; a ``None`` leaves the
    stored value untouched (PATCH semantics).
    """
    _apply_profile(
        user,
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
        phone=phone,
        gender=gender,
        photo=photo,
        date_of_birth=date_of_birth,
    )
    user.save(update_fields=[*SELF_EDITABLE_PROFILE_FIELDS, "phone_verified"])
    logger.info("[update_own_profile] user %s", user.pk)
    return user


# ── Self-service email change (re-verify) & phone verification ───────────────
def request_email_change(*, user: User, new_email: str) -> dict:
    """Start an email change: validate the new address and send a code to it.

    The change is not applied until :func:`confirm_email_change`; the new address
    is parked in ``pending_email``. Local (DB) accounts only — for every other
    provider the address comes from the provider.

    The e-mail address is the account's recovery channel; moving it must be
    backed by a second factor. The client greys the action out with the same
    explanation, but this is the rule.
    """
    if user.auth_provider != AuthProviderChoices.DB.value:
        raise ValidationError(
            "Your email is managed by your sign-in provider and can't be changed here.", code=9002
        )
    if not user.mfa_enabled:
        raise ValidationError(
            "Turn on two-factor authentication before changing your email address.",
            code=MFA_NEEDED_FOR_EMAIL_CHANGE,
        )
    new_email = validate_email_address(new_email)  # format + disposable, lower-cased
    if new_email == user.email:
        raise ValidationError("That is already your email address.", code=9002)
    ensure_email_free(new_email, exclude_pk=user.pk)

    user.pending_email = new_email
    user.save(update_fields=["pending_email"])
    outcome = otp_services.issue_otp(
        user,
        purpose=otp_services.PURPOSE_EMAIL_CHANGE,
        to_email=new_email,
        to_phone="",
        subject=f"Confirm your new {settings.PROJECT_NAME} email",
    )
    logger.info("[request_email_change] user %s", user.pk)
    return outcome


def confirm_email_change(*, user: User, code: str) -> User:
    """Confirm the pending email with its code and switch the account over."""
    if not user.pending_email:
        raise ValidationError("There is no pending email change to confirm.", code=9002)
    # Re-check uniqueness at confirm time (someone may have taken it meanwhile).
    ensure_email_free(user.pending_email, exclude_pk=user.pk)

    otp_services.validate_otp(user, code, purpose=otp_services.PURPOSE_EMAIL_CHANGE)
    old_email = user.email
    user.email = user.pending_email
    user.pending_email = ""
    user.is_verified = True  # proven by this code
    user.save(update_fields=["email", "pending_email", "is_verified"])
    signals.announce(signals.email_changed, user=user, old_email=old_email)
    logger.info("[confirm_email_change] user %s", user.pk)
    return user


def request_phone_verification(*, user: User) -> dict:
    """Send a code to the caller's current phone to (re)prove it.

    The code also goes to the account's e-mail: a phone verification that
    depends on an SMS gateway alone cannot be completed when the gateway is
    down — this way the person can still finish, and the outcome message tells
    them which channel actually delivered.
    """
    if not user.phone:
        raise ValidationError("Add a phone number to your profile first.", code=9005)
    if user.phone_verified:
        raise ValidationError("Your phone number is already verified.", code=9002)
    outcome = otp_services.issue_otp(
        user,
        purpose=otp_services.PURPOSE_PHONE_VERIFY,
        to_phone=user.phone,
        subject=f"Verify your {settings.PROJECT_NAME} phone number",
    )
    logger.info("[request_phone_verification] user %s", user.pk)
    return outcome


def confirm_phone_verification(*, user: User, code: str) -> User:
    """Confirm the phone code and set ``phone_verified``."""
    otp_services.validate_otp(user, code, purpose=otp_services.PURPOSE_PHONE_VERIFY)
    if not user.phone_verified:
        user.phone_verified = True
        user.save(update_fields=["phone_verified"])
    logger.info("[confirm_phone_verification] user %s", user.pk)
    return user


# ── Data-subject rights: export + deletion ───────────────────────────────────
def export_my_data(*, user: User) -> dict:
    """A portable snapshot of the caller's personal data (data portability)."""
    return data_export.build(user)


def request_account_deletion(*, user: User) -> dict:
    """Step one of self-service deletion: mail (and, when verified, text) a
    one-time code that :func:`delete_my_account` requires.

    Works for every provider — an external or directory account has no local
    password to confirm with, and a mailed code is a stronger signal than a
    password the browser may have auto-filled anyway.
    """
    return otp_services.issue_otp(
        user,
        purpose=otp_services.PURPOSE_ACCOUNT_DELETE,
        to_phone=user.phone if user.phone_verified else "",
        subject=f"Confirm the deletion of your {settings.PROJECT_NAME} account",
    )


def delete_my_account(*, user: User, password: str = "", code: str = "") -> User:
    """Self-service account deletion (soft delete + sign out everywhere).

    Confirmation is the one-time code from :func:`request_account_deletion`; a
    local account may still confirm with its password. The goodbye receipt goes
    out *before* the row is soft-deleted so it reaches the address on file. The
    row is kept for audit and legal hold.
    """
    if code:
        otp_services.validate_otp(user, code, purpose=otp_services.PURPOSE_ACCOUNT_DELETE)
    elif user.auth_provider == AuthProviderChoices.DB.value and password:
        if not user.check_password(password):
            raise ValidationError("Please enter your current password to confirm.", code=9011)
    else:
        raise ValidationError(
            "Enter the confirmation code we sent to your email to delete your account.", code=9005
        )
    _delete(user, by_admin=False)
    logger.info("[delete_my_account] user %s self-deleted", user.pk)
    return user


def _delete(user: User, *, by_admin: bool) -> None:
    account_notices.notify_account_deleted(user, by_admin=by_admin)
    signals.announce(signals.account_gated, user=user, reason=signals.GATE_DELETED)
    user.delete()  # soft delete (deleted=True, is_active=False)
    signals.announce(signals.account_deleted, user=user, by_admin=by_admin)


# ── Administration ───────────────────────────────────────────────────────────
def _refuse_own_account(user: User, actor, what: str) -> None:
    """Operators cannot gate themselves: that is how a system ends up with
    nobody able to sign in and undo it."""
    if actor is not None and user.pk == actor.pk:
        raise ValidationError(f"You cannot {what} your own account here.", code=OWN_ACCOUNT)


def update_user(
    *,
    uid,
    actor=None,
    first_name=None,
    middle_name=None,
    last_name=None,
    phone=None,
    gender=None,
    photo=None,
    date_of_birth=None,
    account_type=None,
) -> User:
    """An administrator corrects a person's details (PATCH: ``None`` = keep)."""
    user = selectors.get_user(uid)
    _apply_profile(
        user,
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
        phone=phone,
        gender=gender,
        photo=photo,
        date_of_birth=date_of_birth,
    )
    account_type = to_values(account_type)
    if account_type is not None:
        if account_type not in AccountTypeChoices.values():
            raise ValidationError("Invalid account type", code=9002)
        if actor is not None and user.pk == actor.pk and account_type != user.account_type:
            raise ValidationError("You cannot change your own account type.", code=OWN_ACCOUNT)
        user.account_type = account_type
    user.save(update_fields=[*SELF_EDITABLE_PROFILE_FIELDS, "phone_verified", "account_type"])
    logger.info("[update_user] user %s by %s", user.pk, getattr(actor, "pk", None))
    return user


def set_user_active(*, uid, active: bool, actor) -> User:
    """Activate or deactivate another user's account.

    Deactivation keeps the row (and its audit/history) — it just blocks sign-in.
    """
    user = selectors.get_user(uid)
    _refuse_own_account(user, actor, "change the active status of")

    if active:
        user.activate()
    else:
        user.deactivate()
        # A deactivated account keeps no live credential.
        signals.announce(signals.account_gated, user=user, reason=signals.GATE_DEACTIVATED)
    logger.info("[set_user_active] user %s -> active=%s by %s", user.pk, active, getattr(actor, "pk", None))
    return user


def set_account_status(*, uid, status: str, actor) -> User:
    """Suspend / block / reinstate another user's account.

    Distinct from soft-delete (which removes the row from listings) and from
    ``is_active``: this is the visible, reversible policy gate checked at
    sign-in and on every request.
    """
    status = to_values(status)
    if status not in AccountStatusChoices.values():
        raise ValidationError("Invalid account status", code=9002)
    user = selectors.get_user(uid)
    _refuse_own_account(user, actor, "change the status of")
    user.set_account_status(status)
    if status != AccountStatusChoices.ACTIVE.value:
        # Suspension/blocking takes effect *now*, not when a token expires.
        reason = (
            signals.GATE_BLOCKED if status == AccountStatusChoices.BLOCKED.value else signals.GATE_SUSPENDED
        )
        signals.announce(signals.account_gated, user=user, reason=reason)
    logger.info("[set_account_status] user %s -> %s by %s", user.pk, status, getattr(actor, "pk", None))
    return user


def soft_delete_user(*, uid, actor) -> User:
    """Soft-delete a user: flag the row, block sign-in, but keep it for audit."""
    user = selectors.get_user(uid)
    _refuse_own_account(user, actor, "delete")
    _delete(user, by_admin=True)
    logger.info("[soft_delete_user] user %s by %s", user.pk, getattr(actor, "pk", None))
    return user


def restore_user(*, uid) -> User:
    """Reverse a soft delete and reactivate the account."""
    user = selectors.get_user(uid, include_deleted=True)
    if not user.deleted:
        raise ValidationError("This account is not deleted.", code=9002)
    user.deleted = False
    user.deleted_at = None
    user.is_active = True
    user.deactivated_at = None
    user.save(update_fields=["deleted", "deleted_at", "is_active", "deactivated_at"])
    logger.info("[restore_user] user %s", user.pk)
    return user


def set_user_roles(*, user_uid, role_uids: list, actor) -> User:
    """Set a user's roles to exactly the supplied set — any account, any type."""
    from apps.rbac import services as rbac_services

    user = selectors.get_user(user_uid)
    rbac_services.set_user_roles(user=user, role_uids=role_uids, actor=actor)
    return user


# ── Housekeeping ─────────────────────────────────────────────────────────────
def purge_stale_unverified_accounts() -> dict:
    """Hard-delete abandoned never-verified self-registrations and clear old codes.

    A local account that was never email-verified and is older than
    ``UNVERIFIED_ACCOUNT_TTL_DAYS`` is a dead sign-up — it cannot sign in and it
    permanently reserves its email and phone. It is purged so those are freed.

    Guardrails: only External, self-registered local accounts that never signed
    in; never staff, never an account an administrator created (those wait for
    their invitation). Used and expired code rows past ``OTP_RETENTION_DAYS``
    are cleared regardless.
    """
    account_ttl = int(getattr(settings, "UNVERIFIED_ACCOUNT_TTL_DAYS", 7))
    stale = User.all_objects.filter(
        is_verified=False,
        auth_provider=AuthProviderChoices.DB.value,
        account_type=AccountTypeChoices.External.value,
        is_staff=False,
        is_superuser=False,
        invited_at__isnull=True,
        last_login__isnull=True,
        date_joined__lt=timezone.now() - timedelta(days=account_ttl),
    )
    accounts = 0
    for user in stale:
        user.hard_delete()
        accounts += 1

    codes = otp_services.purge_old_codes()
    logger.info("[purge_stale_unverified_accounts] accounts=%s codes=%s", accounts, codes)
    return {"accounts": accounts, "otps": codes}
