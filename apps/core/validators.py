"""Input validators shared by every app.

Identity data (names, phone numbers, e-mail addresses, dates of birth) is
validated here once, so registration, profile edits, admin-created accounts and
any business form normalise the same way and cannot drift apart.
"""

import re
from datetime import date, datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email as _django_validate_email
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.exceptions import ValidationError as ApplicationValidationError
from apps.core.hooks import policy

SPECIAL_CHARACTERS = "!@#$%^&*()-_=+[]{};:,<.>/?`~"


class StrongPasswordValidator:
    """Enforce a strong password policy.

    A minimum length (``PASSWORD_MIN_LENGTH``, adjustable at runtime from the
    System settings screen) and at least one upper-case letter, one lower-case
    letter, one digit and one special character. Also forbids embedding the
    username or the local-part of the email.
    """

    @staticmethod
    def min_length() -> int:
        try:
            return max(8, int(policy("PASSWORD_MIN_LENGTH", 8)))
        except (TypeError, ValueError):
            return 8

    def validate(self, password, user=None):
        errors = []
        minimum = self.min_length()
        if len(password) < minimum:
            errors.append(_("Password must be at least %(n)d characters long.") % {"n": minimum})
        if not any(c.isupper() for c in password):
            errors.append(_("Password must contain at least one uppercase letter."))
        if not any(c.islower() for c in password):
            errors.append(_("Password must contain at least one lowercase letter."))
        if not any(c.isdigit() for c in password):
            errors.append(_("Password must contain at least one digit."))
        if not any(c in SPECIAL_CHARACTERS for c in password):
            errors.append(_("Password must contain at least one special character."))

        if user is not None:
            lowered = password.lower()
            username = (getattr(user, "username", "") or "").lower()
            email = getattr(user, "email", "") or ""
            if username and len(username) >= 3 and username in lowered:
                errors.append(_("Password cannot contain your username."))
            local_part = email.split("@")[0].lower()
            if local_part and len(local_part) >= 3 and local_part in lowered:
                errors.append(_("Password cannot contain part of your email address."))

        if errors:
            raise ValidationError(errors)

    def get_help_text(self):
        return _(
            "Your password must be at least %(n)d characters long and include an "
            "uppercase letter, a lowercase letter, a number and a special "
            "character, and must not contain your username or email."
        ) % {"n": self.min_length()}


# ── Date ranges (list filters) ────────────────────────────────────────────────
def _as_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        value = timezone.localtime(value) if timezone.is_aware(value) else value
        return value.date()
    if isinstance(value, date):
        return value
    raise ApplicationValidationError("Dates must be sent as ISO dates", code=9002)


def validate_date_range(date_from, date_to, *, allow_future: bool = False, today=None):
    """Guard a ``date_from`` / ``date_to`` filter pair.

    Native ``min``/``max`` on a date picker only steer the widget — a typed or
    pasted value sails past them — so the API refuses what the UI cannot
    guarantee: ``date_from`` after ``date_to`` (9002), and, for logs, any bound
    in the future (9002, unless ``allow_future``). Either side may be ``None``.
    Returns the pair unchanged so a filter builder can use it inline.
    """
    start, end = _as_date(date_from), _as_date(date_to)
    if start and end and start > end:
        raise ApplicationValidationError("The start date must be on or before the end date", code=9002)
    if not allow_future:
        limit = today or timezone.localdate()
        for bound in (start, end):
            if bound and bound > limit:
                raise ApplicationValidationError("Dates in the future are not allowed here", code=9002)
    return date_from, date_to


# ── Phone numbers ─────────────────────────────────────────────────────────────
# E.164: a leading country code (1–3 digits, no leading zero) then the subscriber
# number, 8–15 digits total. The normalised "+<digits>" form is what is stored.
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")

# Exact national-number length per country code, where the numbering plan is
# fixed. Generic E.164 is too loose for these: it accepts +2557551234, which no
# Tanzanian network issues. Extend with the PHONE_NATIONAL_LENGTHS setting.
_NATIONAL_LENGTHS = {"255": 9, "254": 9, "256": 9, "250": 9, "257": 8}


def _default_country_code() -> str:
    return re.sub(r"\D", "", str(getattr(settings, "DEFAULT_PHONE_COUNTRY_CODE", "") or ""))


def _national_lengths() -> dict:
    extra = getattr(settings, "PHONE_NATIONAL_LENGTHS", None) or {}
    return {**_NATIONAL_LENGTHS, **{str(k): int(v) for k, v in extra.items()}}


def normalize_phone(raw: str) -> str:
    """Return a normalised E.164 phone (``+<digits>``) or raise ``ValidationError``.

    Accepts common local shapes and upgrades them with the project's default
    country code (``DEFAULT_PHONE_COUNTRY_CODE``): ``0755123456`` →
    ``+255755123456``, ``755123456`` → ``+255755123456``, ``00255…`` → ``+255…``.
    Anything already in international form is validated as-is.
    """
    if not raw:
        raise ApplicationValidationError("A phone number is required", code=9021)
    cleaned = re.sub(r"[\s\-().]", "", str(raw).strip())
    country = _default_country_code()
    lengths = _national_lengths()
    national = lengths.get(country)

    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    elif cleaned.startswith("+"):
        pass
    elif country and national and cleaned.startswith("0") and len(cleaned) == national + 1:
        cleaned = "+" + country + cleaned[1:]  # local number typed with the trunk 0
    elif country and national and len(cleaned) == national and not cleaned.startswith("0"):
        # Local number typed with the leading 0 already dropped. Without this
        # upgrade the bare digits would be read as a foreign country code.
        cleaned = "+" + country + cleaned
    else:
        cleaned = "+" + cleaned  # bare digits that already carry a country code

    for code, length in lengths.items():
        if cleaned.startswith("+" + code):
            subscriber = cleaned[len(code) + 1 :]
            if not re.fullmatch(rf"[1-9]\d{{{length - 1}}}", subscriber):
                raise ApplicationValidationError(
                    f"A +{code} number needs exactly {length} digits after the country "
                    "code, without the leading 0.",
                    code=9021,
                )
            return cleaned

    if not _E164_RE.match(cleaned):
        raise ApplicationValidationError(
            "Enter a valid phone number including the country code, e.g. +255755123456.",
            code=9021,
        )
    return cleaned


# ── Names and dates of birth ──────────────────────────────────────────────────
# Human-name shape: unicode letters in words separated by single spaces, hyphens,
# apostrophes or periods (accents, O'Brien, Anne-Marie, St. John all pass) — but
# no digits or symbol noise.
_NAME_RE = re.compile(r"^[^\W\d_]+\.?(?:[ '\-][^\W\d_]+\.?)*$")


def validate_person_name(value: str, *, label: str = "Name", required: bool = True) -> str:
    """Normalise (collapse whitespace) and validate a human name; returns it."""
    value = " ".join((value or "").split())
    if not value:
        if required:
            raise ApplicationValidationError(f"{label} is required", code=9005)
        return ""
    if not 2 <= len(value) <= 60:
        raise ApplicationValidationError(f"{label} must be between 2 and 60 characters", code=9002)
    if not _NAME_RE.match(value):
        raise ApplicationValidationError(
            f"{label} can only contain letters, spaces, hyphens or apostrophes",
            code=9002,
        )
    return value


def validate_date_of_birth(dob) -> None:
    """Reject future dates and implausibly old ages; no hard minimum-age gate."""
    today = timezone.localdate()
    if dob > today:
        raise ApplicationValidationError("Date of birth cannot be in the future", code=9002)
    if (today.year - dob.year) > 120:
        raise ApplicationValidationError("Please enter a valid date of birth", code=9002)


# ── E-mail addresses ──────────────────────────────────────────────────────────
# A starter blocklist of throwaway providers. Extend from the environment via
# DISPOSABLE_EMAIL_DOMAINS (comma-separated) without a code change.
_DISPOSABLE_DOMAINS = {
    "mailinator.com",
    "guerrillamail.com",
    "10minutemail.com",
    "tempmail.com",
    "temp-mail.org",
    "throwawaymail.com",
    "yopmail.com",
    "getnada.com",
    "trashmail.com",
    "sharklasers.com",
    "guerrillamailblock.com",
    "dispostable.com",
    "fakeinbox.com",
    "maildrop.cc",
    "mailnesia.com",
    "mohmal.com",
    "emailondeck.com",
    "tempmailo.com",
    "burnermail.io",
    "temp-mail.io",
    "moakt.com",
    "spam4.me",
}


def is_disposable_email(email: str) -> bool:
    domain = (email or "").rsplit("@", 1)[-1].strip().lower()
    if not domain:
        return False
    extra = getattr(settings, "DISPOSABLE_EMAIL_DOMAINS", None) or []
    blocklist = _DISPOSABLE_DOMAINS | {d.strip().lower() for d in extra if d.strip()}
    return domain in blocklist


def validate_email_address(email: str, *, allow_disposable: bool = False) -> str:
    """Lower-case + format-validate an email; raise on malformed or disposable."""
    email = (email or "").strip().lower()
    if not email:
        raise ApplicationValidationError("An email address is required", code=9005)
    try:
        _django_validate_email(email)
    except ValidationError as exc:
        raise ApplicationValidationError("Enter a valid email address", code=9002) from exc
    if not allow_disposable and is_disposable_email(email):
        raise ApplicationValidationError(
            "Disposable or temporary email addresses are not allowed. Please use a permanent email.",
            code=9019,
        )
    return email
