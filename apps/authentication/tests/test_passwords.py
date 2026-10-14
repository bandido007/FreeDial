"""Passwords: change, forgot, invitation links, history, expiry."""

import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.authentication import password_services
from apps.authentication.models import AuthenticationAudit, PasswordHistory, PasswordResetToken
from apps.core.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def link_in(message) -> str:
    return re.search(r"https?://\S+/set-password/\S+", message.body).group(0).rstrip(".")


def token_of(link: str) -> str:
    return link.rsplit("/", 1)[1]


def test_changing_the_password(user, mailbox, api):
    client = api(user)
    from oauth2_provider.models import get_access_token_model

    other_session = get_access_token_model().objects.create(
        user=user,
        application=get_access_token_model().objects.first().application,
        token="other",
        expires=timezone.now() + timedelta(hours=1),
        scope="read write",
    )
    from django.test import RequestFactory

    request = RequestFactory().post("/graphql", HTTP_AUTHORIZATION=f"Bearer {client.access_token}")
    password_services.change_own_password(
        user=user, old_password="Str0ng!Passw0rd", new_password="An0ther!Passw0rd", request=request
    )
    user.refresh_from_db()
    assert user.check_password("An0ther!Passw0rd") and user.password_changed_at is not None
    assert mailbox[-1].subject.endswith("password was changed")
    assert AuthenticationAudit.objects.filter(user=user, event_type="password_changed").exists()
    # every other device is signed out; this one stays
    assert not get_access_token_model().objects.filter(pk=other_session.pk).exists()
    assert get_access_token_model().objects.filter(token=client.access_token).exists()


def test_change_rules(user, make_user):
    with pytest.raises(ValidationError) as excinfo:
        password_services.change_own_password(
            user=user, old_password="wrong", new_password="An0ther!Passw0rd"
        )
    assert excinfo.value.code == 9011
    with pytest.raises(ValidationError) as excinfo:
        password_services.change_own_password(
            user=user, old_password="Str0ng!Passw0rd", new_password="Str0ng!Passw0rd"
        )
    assert excinfo.value.code == 9016
    with pytest.raises(ValidationError) as excinfo:
        password_services.change_own_password(user=user, old_password="Str0ng!Passw0rd", new_password="weak")
    assert excinfo.value.code == 9010
    external = make_user(auth_provider="GOOGLE")
    with pytest.raises(ValidationError) as excinfo:
        password_services.change_own_password(
            user=external, old_password="x", new_password="An0ther!Passw0rd"
        )
    assert excinfo.value.code == 9123


def test_recent_passwords_cannot_be_reused(user, settings):
    settings.PASSWORD_HISTORY_COUNT = 2
    assert PasswordHistory.objects.filter(user=user).count() == 1  # recorded when the account was made
    password_services.change_own_password(
        user=user, old_password="Str0ng!Passw0rd", new_password="Sec0nd!Passw0rd"
    )
    with pytest.raises(ValidationError) as excinfo:
        password_services.change_own_password(
            user=user, old_password="Sec0nd!Passw0rd", new_password="Str0ng!Passw0rd"
        )
    assert excinfo.value.code == 9120
    password_services.change_own_password(
        user=user, old_password="Sec0nd!Passw0rd", new_password="Th1rd!Passw0rd"
    )
    # the first password fell out of the remembered two
    password_services.change_own_password(
        user=user, old_password="Th1rd!Passw0rd", new_password="Str0ng!Passw0rd"
    )
    assert PasswordHistory.objects.filter(user=user).count() == 2


def test_history_can_be_switched_off(user, settings):
    settings.PASSWORD_HISTORY_COUNT = 0
    from apps.authentication.password_validators import PasswordHistoryValidator

    assert PasswordHistoryValidator().get_help_text() == ""
    password_services.change_own_password(
        user=user, old_password="Str0ng!Passw0rd", new_password="Sec0nd!Passw0rd"
    )
    password_services.change_own_password(
        user=user, old_password="Sec0nd!Passw0rd", new_password="Str0ng!Passw0rd"
    )


def test_forgot_password_is_privacy_preserving(user, make_user, mailbox):
    outcome = password_services.request_password_reset(email="nobody@example.org")
    assert "If an account exists" in outcome["message"] and not outcome["email_ok"] and mailbox == []
    external = make_user(auth_provider="GOOGLE")
    assert not password_services.request_password_reset(email=external.email)["email_ok"]
    with pytest.raises(ValidationError):
        password_services.request_password_reset(email="")

    outcome = password_services.request_password_reset(email=user.email.upper(), ip="203.0.113.9")
    assert outcome["email_ok"] and outcome["message"] == password_services.REQUEST_ACK
    token = PasswordResetToken.objects.get(user=user)
    assert token.requested_ip == "203.0.113.9" and token.purpose == "reset"
    assert token_of(link_in(mailbox[0])) not in token.token_hash


def test_the_reset_link_sets_the_password_once(user, mailbox, sign_in):
    sign_in(user.email)
    password_services.request_password_reset(email=user.email)
    raw = token_of(link_in(mailbox[0]))

    with pytest.raises(ValidationError) as excinfo:
        password_services.set_password_with_token(token=raw, new_password="weak")
    assert excinfo.value.code == 9010
    with pytest.raises(ValidationError) as excinfo:
        password_services.set_password_with_token(token=raw, new_password="Str0ng!Passw0rd")
    assert excinfo.value.code == 9016

    password_services.set_password_with_token(token=raw, new_password="Fr3sh!Passw0rd")
    user.refresh_from_db()
    assert user.check_password("Fr3sh!Passw0rd") and user.is_verified
    from oauth2_provider.models import get_access_token_model

    assert not get_access_token_model().objects.filter(user=user).exists()  # signed out everywhere
    assert AuthenticationAudit.objects.filter(user=user, event_type="password_reset_completed").exists()

    with pytest.raises(ValidationError) as excinfo:
        password_services.set_password_with_token(token=raw, new_password="Ag4in!Passw0rd")
    assert excinfo.value.code == 9017


def test_expired_unknown_and_empty_links_are_refused(user, mailbox):
    password_services.request_password_reset(email=user.email)
    raw = token_of(link_in(mailbox[0]))
    PasswordResetToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    with pytest.raises(ValidationError) as excinfo:
        password_services.set_password_with_token(token=raw, new_password="Fr3sh!Passw0rd")
    assert excinfo.value.code == 9017
    with pytest.raises(ValidationError):
        password_services.set_password_with_token(token="nonsense", new_password="Fr3sh!Passw0rd")
    with pytest.raises(ValidationError) as excinfo:
        password_services.set_password_with_token(token="", new_password="")
    assert excinfo.value.code == 9005


def test_a_new_request_retires_the_previous_link(user, mailbox):
    password_services.request_password_reset(email=user.email)
    first = token_of(link_in(mailbox[0]))
    password_services.request_password_reset(email=user.email)
    with pytest.raises(ValidationError):
        password_services.set_password_with_token(token=first, new_password="Fr3sh!Passw0rd")


def test_an_invitation_activates_the_account(admin, mailbox):
    from apps.users import registration_services

    person, _ = registration_services.create_user(
        email="new@example.org", first_name="New", last_name="Person"
    )
    raw = token_of(link_in(mailbox[0]))
    assert PasswordResetToken.objects.get(user=person).purpose == "invite"

    password_services.set_password_with_token(token=raw, new_password="Fr3sh!Passw0rd")
    person.refresh_from_db()
    assert person.is_verified and not person.invitation_pending and person.check_password("Fr3sh!Passw0rd")
    assert any(message.subject.startswith("Welcome") for message in mailbox)


def test_an_administrator_can_send_a_reset_and_require_a_change(user, admin, mailbox, make_user):
    assert password_services.admin_send_password_reset(user=user, actor=admin)
    assert mailbox[0].to == [user.email]
    external = make_user(auth_provider="LDAP")
    with pytest.raises(ValidationError):
        password_services.admin_send_password_reset(user=external, actor=admin)

    password_services.require_password_change(user=user, actor=admin)
    user.refresh_from_db()
    assert user.must_change_password
    password_services.change_own_password(
        user=user, old_password="Str0ng!Passw0rd", new_password="Fr3sh!Passw0rd"
    )
    user.refresh_from_db()
    assert not user.must_change_password


def test_spent_links_are_purged(user, mailbox):
    password_services.request_password_reset(email=user.email)
    PasswordResetToken.objects.update(is_used=True, updated_at=timezone.now() - timedelta(days=8))
    assert password_services.purge_spent_tokens() == 1
