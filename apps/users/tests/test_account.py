"""Self-service: profile, e-mail change, phone verification, consent, data rights."""

import re

import pytest

from apps.core.exceptions import DuplicateError, ValidationError
from apps.users import consent_services, data_export, services, signals

pytestmark = pytest.mark.django_db


def code_in(message: str) -> str:
    return re.search(r"\b(\d{6})\b", message).group(1)


def test_profile_edits_are_validated_and_partial(user, make_user):
    other = make_user(phone="+255755999999")
    services.update_own_profile(user=user, first_name="  Asha ", photo="/media/profiles/x.png")
    user.refresh_from_db()
    assert user.first_name == "Asha" and user.last_name  # untouched
    with pytest.raises(ValidationError):
        services.update_own_profile(user=user, last_name="")
    with pytest.raises(ValidationError):
        services.update_own_profile(user=user, gender="?")
    with pytest.raises(DuplicateError):
        services.update_own_profile(user=user, phone=other.phone)


def test_changing_the_phone_number_drops_its_proof(user):
    user.phone_verified = True
    user.save()
    services.update_own_profile(user=user, phone="0755777777")
    user.refresh_from_db()
    assert user.phone == "+255755777777" and not user.phone_verified


def test_profile_completeness_follows_the_policy(user, settings):
    settings.PROFILE_REQUIRED_FIELDS = ["first_name", "date_of_birth"]
    assert user.missing_profile_fields() == ["date_of_birth"]
    assert not user.is_profile_complete
    assert user.missing_profile_fields(required=["first_name"]) == []


def test_phone_verification(user, mailbox):
    outcome = services.request_phone_verification(user=user)
    assert outcome["email_ok"]  # the code also goes to the address on file
    services.confirm_phone_verification(user=user, code=code_in(mailbox[0].body))
    user.refresh_from_db()
    assert user.phone_verified
    with pytest.raises(ValidationError):
        services.request_phone_verification(user=user)  # already verified
    user.phone = ""
    with pytest.raises(ValidationError):
        services.request_phone_verification(user=user)


def test_email_change_needs_two_factor_goes_to_the_new_address_and_signs_out(user, make_user, mailbox):
    with pytest.raises(ValidationError) as excinfo:
        services.request_email_change(user=user, new_email="new@example.org")
    assert excinfo.value.code == 9104

    user.mfa_enabled = True
    user.save()
    taken = make_user()
    with pytest.raises(DuplicateError):
        services.request_email_change(user=user, new_email=taken.email)
    with pytest.raises(ValidationError):
        services.request_email_change(user=user, new_email=user.email)

    services.request_email_change(user=user, new_email="New@Example.org")
    user.refresh_from_db()
    assert user.pending_email == "new@example.org" and mailbox[0].to == ["new@example.org"]

    heard = []
    signals.email_changed.connect(lambda sender, **kwargs: heard.append(kwargs["old_email"]), weak=False)
    services.confirm_email_change(user=user, code=code_in(mailbox[0].body))
    user.refresh_from_db()
    assert user.email == "new@example.org" and user.pending_email == "" and user.is_verified
    assert heard == ["person@example.org"]
    with pytest.raises(ValidationError):
        services.confirm_email_change(user=user, code="123456")  # nothing pending


def test_external_accounts_manage_their_email_and_password_elsewhere(make_user):
    google = make_user(auth_provider="GOOGLE", mfa_enabled=True)
    with pytest.raises(ValidationError):
        services.request_email_change(user=google, new_email="x@example.org")


def test_consent_is_versioned_and_asked_again(user, settings):
    settings.CONSENT_TERMS_VERSION, settings.CONSENT_PRIVACY_VERSION = "2026-01", "2026-01"
    assert consent_services.pending_consents(user) == ["TERMS", "PRIVACY"]
    consent_services.accept_current_consents(user=user, ip="203.0.113.9", user_agent="test")
    assert consent_services.pending_consents(user) == []
    assert consent_services.has_accepted_current(user, "TERMS")

    settings.CONSENT_TERMS_VERSION = "2026-09"
    assert consent_services.pending_consents(user) == ["TERMS"]
    consent_services.accept_current_consents(user=user)
    assert user.consents.count() == 3


def test_data_export_gathers_every_section(user, grant):
    grant(user, "can_view_users")
    data_export.register_section("widgets", lambda person: {"count": 3})
    data_export.register_section("broken", lambda person: 1 / 0)
    try:
        snapshot = services.export_my_data(user=user)
    finally:
        data_export._sections.pop("widgets"), data_export._sections.pop("broken")
    assert snapshot["profile"]["email"] == user.email
    assert snapshot["roles"] == ["test-role-1"]
    assert snapshot["widgets"] == {"count": 3}
    assert "error" in snapshot["broken"]
    assert {"login_history", "security_events", "consents"} <= set(snapshot)


def test_deleting_my_account_needs_a_code_or_the_password(user, mailbox):
    with pytest.raises(ValidationError) as excinfo:
        services.delete_my_account(user=user)
    assert excinfo.value.code == 9005
    with pytest.raises(ValidationError) as excinfo:
        services.delete_my_account(user=user, password="wrong")
    assert excinfo.value.code == 9011

    services.request_account_deletion(user=user)
    heard = []
    signals.account_deleted.connect(lambda sender, **kwargs: heard.append(kwargs["by_admin"]), weak=False)
    services.delete_my_account(user=user, code=code_in(mailbox[0].body))
    user.refresh_from_db()
    assert user.deleted and not user.is_active and heard == [False]
    assert any("deleted" in message.subject for message in mailbox)


def test_deleting_with_the_password(user, mailbox):
    services.delete_my_account(user=user, password="Str0ng!Passw0rd")
    assert user.deleted
