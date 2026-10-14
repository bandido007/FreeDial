"""Restrictions (password expiry, two-factor policy), two-factor services, sessions."""

from datetime import timedelta

import pytest
from django.test import RequestFactory
from django.utils import timezone

from apps.authentication import gates, mfa_services, session_services, tokens, totp
from apps.authentication.models import MfaCredential, MfaRecoveryCode
from apps.core.exceptions import NotFoundError, ValidationError

pytestmark = pytest.mark.django_db


def current_code(secret: str) -> str:
    return totp._hotp(secret, totp.current_step())


def enrol(user):
    secret = mfa_services.begin_enrollment(user=user)["secret"]
    codes = mfa_services.confirm_enrollment(user=user, code=current_code(secret))
    return secret, codes


# ── Two-factor ───────────────────────────────────────────────────────────────
def test_enrolment_needs_the_first_code_and_hands_out_recovery_codes(user):
    setup = mfa_services.begin_enrollment(user=user)
    from urllib.parse import quote

    from django.conf import settings

    assert setup["otpauth_uri"].startswith(f"otpauth://totp/{quote(settings.PROJECT_NAME)}%3A")
    assert not user.mfa_enabled and not MfaCredential.objects.get(user=user).is_confirmed
    with pytest.raises(ValidationError):
        mfa_services.confirm_enrollment(user=user, code="000000")

    codes = mfa_services.confirm_enrollment(user=user, code=current_code(setup["secret"]))
    user.refresh_from_db()
    assert user.mfa_enabled and len(codes) == 10 and all("-" in code for code in codes)
    assert MfaRecoveryCode.objects.filter(user=user, used_at__isnull=True).count() == 10
    assert not any(code in row.code_hash for row in MfaRecoveryCode.objects.all() for code in codes)
    from apps.notifications.models import Notification

    assert Notification.objects.get(receiver=user).subject == "Two-factor authentication was turned on"
    with pytest.raises(ValidationError):
        mfa_services.begin_enrollment(user=user)  # already on


def test_a_code_cannot_be_replayed(user):
    secret, _ = enrol(user)
    code = totp._hotp(secret, totp.current_step() + 1)
    assert mfa_services.code_ok(user, code)
    assert not mfa_services.code_ok(user, code)  # the same code, again
    assert not mfa_services.code_ok(user, "")
    assert not mfa_services.code_ok(user, "12 34")


def test_recovery_codes_are_single_use_and_regenerable(user):
    secret, codes = enrol(user)
    assert mfa_services.code_ok(user, codes[0])
    assert not mfa_services.code_ok(user, codes[0])
    assert mfa_services.recovery_codes_remaining(user) == 9

    fresh = mfa_services.regenerate_recovery_codes(
        user=user, code=totp._hotp(secret, totp.current_step() + 1)
    )
    assert not mfa_services.code_ok(user, codes[1])  # the old set is gone
    assert mfa_services.code_ok(user, fresh[0].replace("-", "").upper())
    with pytest.raises(ValidationError):
        mfa_services.regenerate_recovery_codes(user=user, code="000000")


def test_two_factor_can_be_turned_off_unless_policy_requires_it(user, settings):
    secret, _ = enrol(user)
    settings.MFA_REQUIRED_ACCOUNT_TYPES = ["External"]
    with pytest.raises(ValidationError) as excinfo:
        mfa_services.disable(user=user, code=current_code(secret))
    assert excinfo.value.code == 9122

    settings.MFA_REQUIRED_ACCOUNT_TYPES = []
    with pytest.raises(ValidationError):
        mfa_services.disable(user=user, code="000000")
    with pytest.raises(ValidationError):  # older than the last accepted step: a replay
        mfa_services.disable(user=user, code=totp._hotp(secret, totp.current_step() - 1))
    mfa_services.disable(user=user, code=totp._hotp(secret, totp.current_step() + 1))
    user.refresh_from_db()
    assert not user.mfa_enabled and not MfaCredential.objects.filter(user=user).exists()
    with pytest.raises(ValidationError) as excinfo:
        mfa_services.disable(user=user, code="000000")
    assert excinfo.value.code == 9121


def test_an_administrator_resets_two_factor_for_a_locked_out_person(user, admin, api):
    api(user)  # a live session, opened before two-factor was turned on
    enrol(user)
    mfa_services.admin_reset(user=user, actor=admin)
    user.refresh_from_db()
    assert not user.mfa_enabled
    assert not tokens.get_access_token_model().objects.filter(user=user).exists()


def test_tickets_carry_an_identity_for_a_few_attempts(user):
    ticket = mfa_services.issue_ticket(user, provider="google")
    assert mfa_services.resolve_ticket(ticket) == (user, "google")
    assert mfa_services.resolve_ticket("nope") == (None, "")
    for _ in range(mfa_services.TICKET_MAX_ATTEMPTS - 1):
        assert mfa_services.record_ticket_attempt(ticket)
    assert not mfa_services.record_ticket_attempt(ticket)  # burnt
    assert mfa_services.resolve_ticket(ticket) == (None, "")
    assert not mfa_services.record_ticket_attempt("gone")


def test_the_totp_helpers():
    secret = totp.generate_secret()
    assert totp.verify(secret, current_code(secret))
    assert totp.match(secret, current_code(secret), at=None) == totp.current_step()
    assert totp.match(secret, "abc") is None
    assert totp.match("", "123456") is None
    assert totp.match("!!!!", "123456") is None


# ── Restrictions ─────────────────────────────────────────────────────────────
def test_policy_can_require_two_factor_by_account_type_or_role(user, settings, grant):
    assert gates.restriction(user) is None
    settings.MFA_REQUIRED_ACCOUNT_TYPES = ["External"]
    assert gates.restriction(user) == (
        8015,
        "Set up two-factor authentication to continue. Your account requires it.",
    )
    settings.MFA_REQUIRED_ACCOUNT_TYPES = []
    settings.MFA_REQUIRED_ROLES = ["test-role-1"]
    assert gates.restriction(user) is None
    grant(user, "can_view_users")
    assert gates.mfa_required_for(user)
    enrol(user)
    assert gates.restriction(user) is None


def test_an_expired_password_must_be_changed(user, settings):
    settings.PASSWORD_MAX_AGE_DAYS = 30
    assert gates.password_expires_at(user) is not None and not gates.password_expired(user)
    user.password_changed_at = timezone.now() - timedelta(days=31)
    user.save()
    assert gates.password_expired(user)
    assert gates.restriction(user)[0] == 8016

    user.auth_provider = "GOOGLE"
    assert gates.password_expires_at(user) is None
    settings.PASSWORD_MAX_AGE_DAYS = 0
    user.auth_provider = "DB"
    assert gates.password_expires_at(user) is None


def test_a_forced_change_is_a_restriction(user):
    user.must_change_password = True
    assert gates.restriction(user) == (8016, "Choose a new password to continue.")


def test_the_restriction_is_computed_once_per_request(user, settings):
    request = RequestFactory().post("/graphql")
    assert gates.restriction(user, request) is None
    settings.MFA_REQUIRED_ACCOUNT_TYPES = ["External"]
    assert gates.restriction(user, request) is None  # cached
    gates.forget_restriction(request)
    assert gates.restriction(user, request) is not None


def test_a_restricted_session_may_only_comply(gql, user, settings):
    settings.MFA_REQUIRED_ACCOUNT_TYPES = ["External"]
    me = gql(
        "query { meQuery { code data { mfaEnrolmentRequired restrictionCode passwordChangeRequired } } }",
        user=user,
    )
    assert me["data"] == {
        "mfaEnrolmentRequired": True,
        "restrictionCode": 8015,
        "passwordChangeRequired": False,
    }
    refused = gql("query { getMyNotifications { code message } }", user=user)
    assert refused["code"] == 8015
    assert gql("mutation { beginMfaEnrollment { code } }", user=user)["code"] == 9001
    assert gql("query { getRegistrationStatus { code } }")["code"] == 9001


def test_rest_endpoints_refuse_a_restricted_session(api, user, settings):
    client = api(user)
    settings.MFA_REQUIRED_ACCOUNT_TYPES = ["External"]
    response = client.post("/files/download/", data={"uid": "00000000-0000-7000-8000-000000000000"})
    assert response.status_code == 403


# ── Sessions ─────────────────────────────────────────────────────────────────
def test_sessions_are_listed_and_revoked_one_by_one(user, sign_in):
    _, first = sign_in(user.email)
    _, second = sign_in(user.email)
    request = RequestFactory().post("/graphql", HTTP_AUTHORIZATION=f"Bearer {second['access_token']}")
    sessions = session_services.list_sessions(user=user, request=request)
    assert len(sessions) == 2 and sum(session.is_current for session in sessions) == 1
    assert sessions[0].label and sessions[0].provider == "DB"

    other = next(session for session in sessions if not session.is_current)
    assert session_services.revoke_session(user=user, session_id=other.id, request=request) is False
    with pytest.raises(NotFoundError):
        session_services.revoke_session(user=user, session_id=other.id, request=request)
    with pytest.raises(NotFoundError):
        session_services.revoke_session(user=user, session_id="not-a-number")
    assert len(session_services.list_sessions(user=user, request=request)) == 1
    assert session_services.list_sessions(user=user, request=None)[0].is_current is False


def test_sign_out_everywhere_keeps_the_current_session_by_default(user, sign_in):
    _, first = sign_in(user.email)
    _, second = sign_in(user.email)
    request = RequestFactory().post("/graphql", HTTP_AUTHORIZATION=f"Bearer {second['access_token']}")
    assert session_services.revoke_all_my_sessions(user=user, request=request) == 1
    assert [s.access_token for s in ()] == []
    assert tokens.get_access_token_model().objects.filter(user=user).count() == 1
    assert session_services.revoke_all_my_sessions(user=user, request=request, keep_current=False) == 1
    assert not tokens.get_refresh_token_model().objects.filter(user=user).exists()


def test_an_administrator_signs_someone_out(user, admin, sign_in):
    sign_in(user.email)
    assert session_services.revoke_sessions_of(user=user, actor=admin) == 1
    assert session_services.list_sessions(user=user) == []


def test_expired_tokens_are_purged(user, sign_in, settings):
    sign_in(user.email)
    tokens.get_access_token_model().objects.update(expires=timezone.now() - timedelta(days=3))
    tokens.get_refresh_token_model().objects.update(created=timezone.now() - timedelta(days=3))
    assert tokens.purge_expired_tokens() == 2


def test_minting_tokens_needs_the_first_party_application(user):
    from oauth2_provider.models import get_application_model

    get_application_model().objects.all().delete()
    from apps.core.exceptions import ApplicationError

    with pytest.raises(ApplicationError):
        tokens.issue_oauth2_tokens(user)
