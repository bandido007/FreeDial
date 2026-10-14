"""Input validators: dates, phone numbers, names, e-mail addresses, passwords."""

from datetime import UTC, date, datetime

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.core import validators
from apps.core.exceptions import ValidationError

TODAY = date(2026, 9, 14)


# ── Date ranges ──────────────────────────────────────────────────────────────
def test_accepts_open_ended_and_ordered_ranges():
    assert validators.validate_date_range(None, None, today=TODAY) == (None, None)
    validators.validate_date_range(date(2026, 9, 1), None, today=TODAY)
    validators.validate_date_range(None, date(2026, 9, 14), today=TODAY)
    validators.validate_date_range(date(2026, 9, 1), date(2026, 9, 14), today=TODAY)


def test_rejects_inverted_ranges():
    with pytest.raises(ValidationError) as excinfo:
        validators.validate_date_range(date(2026, 9, 10), date(2026, 9, 1), today=TODAY)
    assert excinfo.value.code == 9002


def test_rejects_future_dates_unless_allowed():
    with pytest.raises(ValidationError):
        validators.validate_date_range(None, date(2026, 9, 15), today=TODAY)
    with pytest.raises(ValidationError):
        validators.validate_date_range(datetime(2026, 12, 1, tzinfo=UTC), None, today=TODAY)
    validators.validate_date_range(date(2026, 9, 15), date(2027, 1, 1), today=TODAY, allow_future=True)


def test_rejects_a_value_that_is_not_a_date():
    with pytest.raises(ValidationError):
        validators.validate_date_range("2026-09-01", None, today=TODAY)


# ── Phone numbers ────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0755123456", "+255755123456"),  # local, with the trunk 0
        ("755123456", "+255755123456"),  # local, trunk 0 already dropped
        ("+255 755 123 456", "+255755123456"),
        ("00255755123456", "+255755123456"),
        ("255755123456", "+255755123456"),
        ("+44 20 7946 0958", "+442079460958"),  # another country: generic E.164
    ],
)
def test_phone_numbers_are_normalised(raw, expected):
    assert validators.normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "+2557551234", "+255055123456", "+25575512345678", "12", "abc"])
def test_malformed_phone_numbers_are_refused(raw):
    with pytest.raises(ValidationError) as excinfo:
        validators.normalize_phone(raw)
    assert excinfo.value.code == 9021


def test_default_country_code_is_configurable(settings):
    settings.DEFAULT_PHONE_COUNTRY_CODE = "254"
    assert validators.normalize_phone("0712345678") == "+254712345678"


def test_without_a_default_country_code_a_local_number_is_not_guessed(settings):
    settings.DEFAULT_PHONE_COUNTRY_CODE = ""
    with pytest.raises(ValidationError):
        validators.normalize_phone("0755")


# ── Names and dates of birth ─────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["Asha", "Anne-Marie", "O'Brien", "St. John", "  Juma   Ally ", "Zoë"])
def test_real_names_are_accepted(name):
    assert validators.validate_person_name(name) == " ".join(name.split())


@pytest.mark.parametrize("name", ["A", "R2D2", "John@", "x" * 61, "--"])
def test_noise_is_refused_as_a_name(name):
    with pytest.raises(ValidationError):
        validators.validate_person_name(name)


def test_an_optional_name_may_be_blank():
    assert validators.validate_person_name("", required=False) == ""
    with pytest.raises(ValidationError) as excinfo:
        validators.validate_person_name("", label="First name")
    assert excinfo.value.code == 9005


def test_date_of_birth_must_be_plausible():
    validators.validate_date_of_birth(date(1990, 1, 1))
    with pytest.raises(ValidationError):
        validators.validate_date_of_birth(date(2999, 1, 1))
    with pytest.raises(ValidationError):
        validators.validate_date_of_birth(date(1800, 1, 1))


# ── E-mail addresses ─────────────────────────────────────────────────────────
def test_email_is_lowercased_and_validated():
    assert validators.validate_email_address("  Asha@Example.ORG ") == "asha@example.org"
    with pytest.raises(ValidationError) as excinfo:
        validators.validate_email_address("not-an-address")
    assert excinfo.value.code == 9002
    with pytest.raises(ValidationError) as excinfo:
        validators.validate_email_address("")
    assert excinfo.value.code == 9005


def test_disposable_addresses_are_refused(settings):
    with pytest.raises(ValidationError) as excinfo:
        validators.validate_email_address("someone@mailinator.com")
    assert excinfo.value.code == 9019
    settings.DISPOSABLE_EMAIL_DOMAINS = ["throwaway.test"]
    assert validators.is_disposable_email("x@throwaway.test")
    assert validators.validate_email_address("x@throwaway.test", allow_disposable=True)


# ── Passwords ────────────────────────────────────────────────────────────────
class _Person:
    username = ""
    email = "asha.mkumbo@example.org"


def test_a_strong_password_passes():
    validators.StrongPasswordValidator().validate("Str0ng!Passw0rd", _Person())


@pytest.mark.parametrize(
    "password",
    ["short1!A", "alllowercase1!", "ALLUPPERCASE1!", "NoDigits!!aa", "NoSpecial11aa", "Asha.mkumbo1!"],
)
def test_weak_passwords_are_refused(password, settings):
    settings.PASSWORD_MIN_LENGTH = 10
    with pytest.raises(DjangoValidationError):
        validators.StrongPasswordValidator().validate(password, _Person())


def test_minimum_length_never_drops_below_eight(settings):
    settings.PASSWORD_MIN_LENGTH = 3
    assert validators.StrongPasswordValidator.min_length() == 8
    assert "8 characters" in validators.StrongPasswordValidator().get_help_text()
