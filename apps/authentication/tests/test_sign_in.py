"""Signing in and out: the token endpoints, the gates, the lockout, the audit."""

import json

import pytest
from django.test import Client
from django.utils import timezone

from apps.authentication import mfa_services, totp
from apps.authentication.models import AuthenticationAudit, UserLoginHistory

pytestmark = pytest.mark.django_db


def test_a_password_sign_in_returns_tokens_and_the_person(sign_in, user):
    status, body = sign_in(user.email)
    assert status == 200 and body["success"] and body["code"] == 8000
    assert body["access_token"] and body["refresh_token"] and body["token_type"] == "Bearer"
    assert body["user"]["email"] == user.email and body["user"]["authorities"] == []
    assert body["requirements"] == {
        "mfa_enrolment_required": False,
        "password_change_required": False,
        "password_expires_at": None,
    }
    user.refresh_from_db()
    assert user.last_login is not None
    assert UserLoginHistory.objects.filter(user=user).count() == 1
    assert AuthenticationAudit.objects.filter(user=user, event_type="token_generated").exists()


def test_a_wrong_password_is_refused_and_recorded(sign_in, user):
    status, body = sign_in(user.email, "wrong")
    assert status == 400 and body == {
        "success": False,
        "code": 8002,
        "message": "Invalid username or password. Please check and try again.",
    }
    audit = AuthenticationAudit.objects.get(email=user.email, event_type="failed_login")
    assert audit.status == "failure"


def test_refresh_and_revoke(sign_in, oauth_app, user):
    _, body = sign_in(user.email)
    refreshed = Client().post(
        "/oauth2/access_token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": body["refresh_token"],
            "client_id": oauth_app.client_id,
        },
    )
    assert refreshed.status_code == 200
    fresh = json.loads(refreshed.content)
    assert fresh["access_token"] != body["access_token"]
    assert AuthenticationAudit.objects.filter(user=user, event_type="token_refreshed").exists()
    assert UserLoginHistory.objects.filter(user=user).count() == 1  # a refresh is not a new sign-in

    revoked = Client().post(
        "/oauth2/revoke_token", data={"token": fresh["access_token"], "client_id": oauth_app.client_id}
    )
    assert revoked.status_code == 200
    assert AuthenticationAudit.objects.filter(user=user, event_type="token_revoked").exists()


def test_only_first_party_grants_are_served(oauth_app):
    response = Client().post(
        "/oauth2/access_token", data={"grant_type": "client_credentials", "client_id": oauth_app.client_id}
    )
    assert response.status_code == 400
    assert json.loads(response.content)["error"] == "unsupported_grant_type"


@pytest.mark.parametrize(
    "change,code",
    [
        ({"is_verified": False}, 8011),
        ({"account_status": "SUSPENDED"}, 8012),
        ({"account_status": "BLOCKED"}, 8013),
        ({"is_active": False}, 8010),
    ],
)
def test_gated_accounts_cannot_sign_in(sign_in, make_user, change, code):
    person = make_user(**change)
    status, body = sign_in(person.email)
    assert status == 400 and body["code"] == code
    from oauth2_provider.models import get_access_token_model

    assert not get_access_token_model().objects.filter(user=person).exists()  # nothing minted stays


def test_a_gate_closing_ends_the_session_at_once(api, user, gql):
    client = api(user)
    assert (
        client.post(
            "/graphql",
            data=json.dumps({"query": "query { meQuery { code } }"}),
            content_type="application/json",
        ).json()["data"]["meQuery"]["code"]
        == 9001
    )

    from apps.users import services

    services.set_account_status(uid=user.uid, status="SUSPENDED", actor=None)
    answer = client.post(
        "/graphql", data=json.dumps({"query": "query { meQuery { code } }"}), content_type="application/json"
    )
    assert answer.json()["data"]["meQuery"]["code"] == 9000


def test_too_many_attempts_lock_the_account(sign_in, user, settings):
    settings.AXES_ENABLED = True
    settings.AXES_FAILURE_LIMIT = 3
    for _ in range(3):
        sign_in(user.email, "wrong")
    status, body = sign_in(user.email)  # right password, but locked
    assert status == 400 and body["code"] == 8003
    assert AuthenticationAudit.objects.filter(email=user.email, event_type="account_locked").exists()


def test_too_many_attempts_from_one_address_are_slowed(sign_in, user, settings):
    settings.RATE_LIMITS = {**settings.RATE_LIMITS, "login": (2, 60)}
    sign_in(user.email, "wrong")
    sign_in(user.email, "wrong")
    status, body = sign_in(user.email)
    assert status == 400 and body["code"] == 9018


def test_two_factor_is_asked_for_and_checked(sign_in, user):
    secret = mfa_services.begin_enrollment(user=user)["secret"]
    mfa_services.confirm_enrollment(user=user, code=totp._hotp(secret, totp.current_step()))
    status, body = sign_in(user.email)
    assert status == 400 and body["code"] == 8014

    status, body = sign_in(user.email, otp="000000")
    assert status == 400 and "incorrect" in body["message"]
    assert AuthenticationAudit.objects.filter(user=user, event_type="mfa_failed").exists()

    status, body = sign_in(user.email, otp=totp._hotp(secret, totp.current_step() + 1))
    assert status == 200 and body["user"]["mfa_enabled"] is True


def test_a_new_device_is_announced(sign_in, user, settings):
    settings.NEW_DEVICE_LOGIN_ALERT = True
    first = Client().post(
        "/oauth2/access_token",
        data={
            "grant_type": "password",
            "username": user.email,
            "password": "Str0ng!Passw0rd",
            "client_id": _client_id(),
        },
        HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0) Chrome/128.0",
    )
    assert first.status_code == 200  # the first device is not "new"
    second = Client().post(
        "/oauth2/access_token",
        data={
            "grant_type": "password",
            "username": user.email,
            "password": "Str0ng!Passw0rd",
            "client_id": _client_id(),
        },
        HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 14) Chrome/128.0 Mobile",
    )
    assert second.status_code == 200
    assert UserLoginHistory.objects.filter(user=user, is_new_device=True).count() == 1
    from apps.notifications.models import Notification

    assert Notification.objects.filter(receiver=user, subject__startswith="New sign-in").count() == 1


def _client_id():
    from oauth2_provider.models import get_application_model

    return get_application_model().objects.first().client_id


def test_the_device_description_is_stable_across_browser_versions():
    old = UserLoginHistory.describe("Mozilla/5.0 (Windows NT 10.0) Chrome/127.0")
    new = UserLoginHistory.describe("Mozilla/5.0 (Windows NT 10.0) Chrome/128.0")
    assert old["fingerprint"] == new["fingerprint"]
    assert old["browser"] != new["browser"]
    assert UserLoginHistory(browser="Chrome 128", os="Windows 10").label == "Chrome 128 · Windows 10"
    assert UserLoginHistory().label == "Unknown device"


def test_failure_audits_survive_a_rolled_back_request(user):
    """A wrong two-factor code raises inside a resolver whose transaction is
    rolled back; the record of the failure must still be there afterwards."""
    from django.test import RequestFactory

    from apps.authentication import events
    from apps.core import context

    request = RequestFactory().post("/graphql")
    queue = context.open_after_request_queue()
    try:
        from django.db import transaction

        with pytest.raises(RuntimeError), transaction.atomic():
            events.record_failure(user=user, request=request, error_message="wrong code")
            raise RuntimeError("the resolver failed")
        assert not AuthenticationAudit.objects.filter(user=user).exists()
    finally:
        context.run_after_request_queue(queue)
    assert AuthenticationAudit.objects.filter(user=user, status="failure").exists()


def test_events_are_deferred_only_while_a_request_is_open(user):
    from apps.authentication import events

    events.record_failure(user=user, error_message="outside a request")
    assert AuthenticationAudit.objects.filter(user=user, status="failure").exists()
    assert timezone.now() > AuthenticationAudit.objects.get(user=user).created_at
