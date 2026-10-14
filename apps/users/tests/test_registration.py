"""Sign-up, verification codes, and accounts created by an administrator."""

import re
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.core.exceptions import DuplicateError, PermissionDeniedError, ThrottledError, ValidationError
from apps.users import otp_services, registration_services, services, signals
from apps.users.models import OtpVerification, User, UserConsent

pytestmark = pytest.mark.django_db

NEW = {
    "email": "Asha@Example.org",
    "password": "Str0ng!Passw0rd",
    "first_name": "Asha",
    "last_name": "Mkumbo",
    "phone": "0755123456",
    "accepted_terms": True,
}


def code_in(message: str) -> str:
    return re.search(r"\b(\d{6})\b", message).group(1)


def test_self_registration_creates_a_verified_less_external_account(mailbox):
    user = registration_services.register_local_user(**NEW)
    assert user.email == "asha@example.org"
    assert user.phone == "+255755123456"
    assert (user.account_type, user.auth_provider) == ("External", "DB")
    assert not user.is_verified and user.check_password(NEW["password"])
    assert user.password_changed_at is not None
    assert {c.document for c in UserConsent.objects.filter(user=user)} == {"TERMS", "PRIVACY"}


def test_self_registration_can_be_closed(settings):
    settings.ALLOW_SELF_REGISTRATION = False
    with pytest.raises(PermissionDeniedError) as excinfo:
        registration_services.register_local_user(**NEW)
    assert excinfo.value.code == 9100
    assert not User.all_objects.filter(email="asha@example.org").exists()


@pytest.mark.parametrize(
    "change,code",
    [
        ({"accepted_terms": False}, 9023),
        ({"email": "asha@mailinator.com"}, 9019),
        ({"password": "weak"}, 9010),
        ({"password": ""}, 9005),
        ({"first_name": "R2D2"}, 9002),
        ({"phone": "12"}, 9021),
        ({"date_of_birth": timezone.localdate() + timedelta(days=1)}, 9002),
    ],
)
def test_the_onboarding_contract_is_enforced(change, code):
    with pytest.raises(ValidationError) as excinfo:
        registration_services.register_local_user(**{**NEW, **change})
    assert excinfo.value.code == code


def test_email_and_phone_identify_one_person(make_user):
    make_user(email="asha@example.org", phone="+255755123456")
    with pytest.raises(DuplicateError) as excinfo:
        registration_services.register_local_user(**NEW)
    assert excinfo.value.code == 9101
    with pytest.raises(DuplicateError) as excinfo:
        registration_services.register_local_user(**{**NEW, "email": "other@example.org"})
    assert excinfo.value.code == 9020


def test_registration_announces_itself():
    heard = []
    signals.user_registered.connect(lambda sender, **kwargs: heard.append(kwargs["user"].email), weak=False)
    registration_services.register_local_user(**NEW, ip="203.0.113.9")
    assert heard == ["asha@example.org"]


# ── Codes ────────────────────────────────────────────────────────────────────
def test_the_code_is_sent_stored_hashed_and_verifies_the_account(mailbox):
    user = registration_services.register_local_user(**NEW)
    outcome = otp_services.issue_registration_otp(user)
    assert outcome["email_ok"] and outcome["sms_ok"]
    assert "both your email and your phone" in outcome["message"]
    code = code_in(mailbox[0].body)
    stored = OtpVerification.objects.get(user=user)
    assert code not in stored.code_hash and stored.channels_sent == {"email": True, "sms": True}

    verified = otp_services.validate_registration_otp(email="ASHA@example.org", code=code)
    assert verified.is_verified and verified.phone_verified
    assert mailbox[-1].subject.startswith("Welcome")
    with pytest.raises(ValidationError) as excinfo:  # a code is single-use
        otp_services.validate_registration_otp(email=user.email, code=code)
    assert excinfo.value.code == 9013


def test_the_message_says_which_channel_delivered(mailbox, settings):
    settings.SMS_ALLOW_LOG_PROVIDER = False
    user = registration_services.register_local_user(**NEW)
    outcome = otp_services.issue_registration_otp(user)
    assert outcome["email_ok"] and not outcome["sms_ok"]
    assert "couldn't reach your phone" in outcome["message"]
    assert otp_services._smart_message(email_attempted=True, email_ok=False, sms_attempted=True, sms_ok=True)
    assert "unavailable" in otp_services._smart_message(
        email_attempted=True, email_ok=False, sms_attempted=False, sms_ok=False
    )


def test_wrong_guesses_are_limited_even_when_the_request_rolls_back(mailbox, settings):
    """Every wrong guess raises, and the API rolls the transaction back: the
    count must survive that, or the limit never bites."""
    from django.db import transaction

    settings.OTP_MAX_ATTEMPTS = 3
    user = registration_services.register_local_user(**NEW)
    otp_services.issue_registration_otp(user)
    code = code_in(mailbox[0].body)
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(3):
        with pytest.raises(ValidationError), transaction.atomic():
            otp_services.validate_registration_otp(email=user.email, code=wrong)
    with pytest.raises(ValidationError) as excinfo, transaction.atomic():
        otp_services.validate_registration_otp(email=user.email, code=code)  # too late, even if right
    assert "Too many attempts" in excinfo.value.message


def test_an_expired_code_is_refused(mailbox):
    user = registration_services.register_local_user(**NEW)
    otp_services.issue_registration_otp(user)
    OtpVerification.objects.filter(user=user).update(expires_at=timezone.now() - timedelta(minutes=1))
    with pytest.raises(ValidationError) as excinfo:
        otp_services.validate_registration_otp(email=user.email, code=code_in(mailbox[0].body))
    assert excinfo.value.code == 9014
    assert OtpVerification.objects.get(user=user).is_used


def test_validation_input_rules(make_user):
    with pytest.raises(ValidationError) as excinfo:
        otp_services.validate_registration_otp(email="", code="123456")
    assert excinfo.value.code == 9005
    with pytest.raises(ValidationError) as excinfo:  # unknown address: same generic answer
        otp_services.validate_registration_otp(email="nobody@example.org", code="123456")
    assert excinfo.value.code == 9013
    user = make_user()
    with pytest.raises(ValidationError):
        otp_services.validate_otp(user, "", purpose="registration")
    with pytest.raises(ValidationError):
        otp_services.validate_otp(user, "123456", purpose="registration")  # none issued


def test_resending_is_rate_limited_and_privacy_preserving(mailbox, settings):
    settings.OTP_RESEND_COOLDOWN_SECONDS = 60
    user = registration_services.register_local_user(**NEW)
    assert "If an account exists" in otp_services.resend_registration_otp("nobody@example.org")["message"]
    outcome = otp_services.resend_registration_otp(user.email)
    assert outcome["email_ok"]
    with pytest.raises(ThrottledError):
        otp_services.resend_registration_otp(user.email)
    cache.clear()
    user.is_verified = True
    user.save()
    assert otp_services.resend_registration_otp(user.email)["message"] == "Your account is already verified."
    with pytest.raises(ValidationError):
        otp_services.resend_registration_otp("")


def test_old_codes_are_purged_with_abandoned_signups(settings, mailbox):
    settings.UNVERIFIED_ACCOUNT_TTL_DAYS = 7
    abandoned = registration_services.register_local_user(**NEW)
    otp_services.issue_registration_otp(abandoned)
    User.all_objects.filter(pk=abandoned.pk).update(date_joined=timezone.now() - timedelta(days=8))
    fresh = registration_services.register_local_user(
        **{**NEW, "email": "fresh@example.org", "phone": "0755000001"}
    )
    otp_services.issue_registration_otp(fresh)
    OtpVerification.objects.update(updated_at=timezone.now() - timedelta(days=10), is_used=True)

    result = services.purge_stale_unverified_accounts()
    assert result == {"accounts": 1, "otps": 1}
    assert not User.all_objects.filter(pk=abandoned.pk).exists()
    assert User.objects.filter(pk=fresh.pk).exists()


# ── Accounts created by an administrator ─────────────────────────────────────
def test_an_administrator_creates_an_account_and_the_owner_is_invited(admin, mailbox, make_role):
    role = make_role("Clerk", ["can_view_users"])
    user, sent = registration_services.create_user(
        email="new@example.org",
        first_name="New",
        last_name="Person",
        phone="0755123457",
        role_uids=[role.uid],
        actor=admin,
    )
    assert sent and user.invited_at is not None and user.invitation_pending
    assert not user.has_usable_password() and user.account_type == "Internal"
    assert user.user_roles.filter(role=role).exists()
    assert mailbox[0].to == ["new@example.org"] and "Set my password" in mailbox[0].alternatives[0][0]
    assert user.auth_audits.filter(event_type="invited").exists()

    with pytest.raises(DuplicateError):
        registration_services.create_user(email="new@example.org", first_name="New", last_name="Person")
    with pytest.raises(ValidationError):
        registration_services.create_user(
            email="x@example.org", first_name="X", last_name="Y", account_type="Alien"
        )
    with pytest.raises(ValidationError):
        registration_services.create_user(email="x@example.org", first_name="X", last_name="Y", gender="?")


def test_invitations_can_be_resent_and_cancelled(admin, mailbox):
    user, _ = registration_services.create_user(email="new@example.org", first_name="New", last_name="Person")
    _, sent = registration_services.resend_invitation(uid=user.uid, actor=admin)
    assert sent and len(mailbox) == 2

    registration_services.cancel_invitation(uid=user.uid, actor=admin)
    assert not User.all_objects.filter(email="new@example.org").exists()

    active, _ = registration_services.create_user(
        email="active@example.org", first_name="Ann", last_name="Bee", send_invitation=False
    )
    active.set_password("Str0ng!Passw0rd")
    active.save()
    with pytest.raises(ValidationError) as excinfo:
        registration_services.resend_invitation(uid=active.uid)
    assert excinfo.value.code == 9103
    with pytest.raises(ValidationError):
        registration_services.cancel_invitation(uid=active.uid)


def test_an_invited_account_is_not_purged_as_abandoned(settings):
    user, _ = registration_services.create_user(
        email="invited@example.org", first_name="Ivy", last_name="Nu", send_invitation=False
    )
    User.all_objects.filter(pk=user.pk).update(
        date_joined=timezone.now() - timedelta(days=30), invited_at=timezone.now() - timedelta(days=30)
    )
    services.purge_stale_unverified_accounts()
    assert User.objects.filter(pk=user.pk).exists()
