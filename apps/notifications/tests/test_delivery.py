"""Sending: e-mail, SMS, in-app — templates, preferences, retries, secrets."""

import pytest

from apps.core.exceptions import ValidationError
from apps.notifications import catalogue, selectors, services, tasks
from apps.notifications.backends import sms as sms_backend
from apps.notifications.enums import NotificationStatusChoices as Status
from apps.notifications.models import Notification, NotificationTemplate

pytestmark = pytest.mark.django_db

EMAIL, SMS, INAPP = "EMAIL", "SMS", "INAPP"


def test_the_declared_categories_are_valid():
    assert catalogue.problems() == []
    codes = {category.code for category in catalogue.categories()}
    assert {"GENERIC", "OTP_VERIFICATION", "PASSWORD_RESET", "ERROR_DIGEST"} <= codes
    assert catalogue.is_mandatory("PASSWORD_CHANGED") and catalogue.is_sensitive("OTP_VERIFICATION")
    assert not catalogue.is_known("NOPE") and catalogue.get("NOPE") is None


def test_an_email_is_sent_inside_the_branded_shell(user, mailbox, settings):
    settings.EMAIL_BRAND = {**settings.EMAIL_BRAND, "portal": "Records Office"}
    row, ok = services.send_now(
        channel=EMAIL, category="GENERIC", subject="Hello", message="A plain message", receiver=user
    )
    assert ok and row.status == Status.SENT.value and row.attempts == 1 and row.sent_time
    assert mailbox[0].to == [user.email] and mailbox[0].subject == "Hello"
    html = mailbox[0].alternatives[0][0]
    assert "Records Office" in html and "A plain message" in html


def test_a_missing_address_is_a_failed_delivery_not_a_crash():
    row, ok = services.send_now(channel=EMAIL, subject="Hello", message="x")
    assert not ok and row.status == Status.FAILED.value
    assert row.remarks == "No recipient e-mail address"


def test_a_backend_that_raises_is_recorded_as_a_failure(user, monkeypatch):
    def explode(notification):
        raise RuntimeError("relay exploded")

    monkeypatch.setattr(services, "dispatch", explode)
    row, ok = services.send_now(channel=EMAIL, subject="Hello", message="x", receiver=user)
    assert not ok and "relay exploded" in row.remarks


def test_sensitive_content_is_scrubbed_after_sending(user, mailbox):
    row, ok = services.send_now(
        channel=EMAIL,
        category="OTP_VERIFICATION",
        subject="Your code",
        message="Your code is 123456",
        receiver=user,
        variables={"otp": "123456"},
        url="https://example.org/reset/secret-token",
    )
    assert ok and "123456" in mailbox[0].body
    row.refresh_from_db()
    assert row.is_redacted and row.message == "[redacted]" and row.variables == {} and row.url == ""
    with pytest.raises(ValidationError) as excinfo:
        services.resend(uid=row.uid)
    assert excinfo.value.code == 9160


def test_saved_copy_wins_over_the_callers_text(user, mailbox):
    services.create_template(
        name="Greeting",
        category="GENERIC",
        channel=EMAIL,
        subject="Hi {full_name}",
        body="Dear {full_name}, {unknown}",
    )
    services.send_now(
        channel=EMAIL,
        category="GENERIC",
        subject="Fallback",
        message="Fallback",
        receiver=user,
        variables={"full_name": "Asha"},
    )
    assert mailbox[0].subject == "Hi Asha"
    assert "Dear Asha, {unknown}" in mailbox[0].body


def test_one_active_template_per_category_and_channel():
    first = services.create_template(name="One", category="GENERIC", channel=EMAIL, body="one")
    second = services.create_template(name="Two", category="GENERIC", channel=EMAIL, body="two")
    first.refresh_from_db()
    assert not first.is_active and second.is_active
    assert selectors.active_template("GENERIC", EMAIL) == second
    services.update_template(uid=first.uid, is_active=True, body="one again")
    assert selectors.active_template("GENERIC", EMAIL).body == "one again"
    services.delete_template(uid=first.uid)
    with pytest.raises(ValidationError):
        services.create_template(name="Bad", category="NOPE", channel=EMAIL)
    with pytest.raises(ValidationError):
        services.create_template(name="Bad", category="GENERIC", channel="FAX")


def test_default_templates_are_installed_once_and_never_overwritten():
    created = services.install_default_templates()
    assert created >= 1
    template = NotificationTemplate.objects.get(category="PASSWORD_CHANGED", channel=SMS)
    template.body = "Edited by an administrator"
    template.save()
    assert services.install_default_templates() == 0
    template.refresh_from_db()
    assert template.body == "Edited by an administrator"
    template.delete()
    assert services.install_default_templates() == 0  # removed on purpose: not put back


def test_queue_delivers_through_the_task(user, mailbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        row = services.queue(channel=EMAIL, category="GENERIC", subject="Queued", message="x", receiver=user)
    row.refresh_from_db()
    assert row.is_sent and len(mailbox) == 1
    assert tasks.send_notification_task(str(row.uid)) is True  # already sent: nothing to do
    assert tasks.send_notification_task("00000000-0000-7000-8000-000000000000") is False


def test_a_broker_outage_does_not_break_the_caller(user, monkeypatch, django_capture_on_commit_callbacks):
    def unreachable(uid):
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.send_notification_task, "delay", unreachable)
    with django_capture_on_commit_callbacks(execute=True):
        row = services.queue(channel=EMAIL, subject="Queued", message="x", receiver=user)
    assert row.status == Status.PENDING.value


def test_fan_out_dedupes_skips_missing_phones_and_resolves_links(
    user, make_user, mailbox, django_capture_on_commit_callbacks
):
    without_phone = make_user(phone="")
    with django_capture_on_commit_callbacks(execute=True):
        queued = services.queue_for(
            [user, user, None, without_phone],
            "GENERIC",
            channels=(INAPP, EMAIL, SMS),
            title="Heads up",
            message="Long body",
            sms_text="Short",
            url=lambda person: f"/people/{person.pk}",
        )
    assert queued == 5  # 2 people × (in-app + e-mail) + 1 SMS
    assert Notification.objects.get(receiver=user, channel=SMS).message == "Short"
    assert Notification.objects.get(receiver=user, channel=INAPP).url == f"/people/{user.pk}"
    assert not Notification.objects.filter(receiver=without_phone, channel=SMS).exists()


def test_a_failing_link_resolver_does_not_stop_the_fan_out(user, django_capture_on_commit_callbacks):
    def broken(person):
        raise KeyError("nope")

    with django_capture_on_commit_callbacks(execute=True):
        assert services.queue_for([user], "GENERIC", channels=(INAPP,), message="x", url=broken) == 1
    assert Notification.objects.get(receiver=user).url == ""


def test_a_person_can_mute_a_category_but_not_a_mandatory_one(user, mailbox):
    services.set_preference(user=user, category="GENERIC", channel=EMAIL, enabled=False)
    row, ok = services.send_now(
        channel=EMAIL, category="GENERIC", subject="Muted", message="x", receiver=user
    )
    assert not ok and row.status == Status.SKIPPED.value and mailbox == []

    with pytest.raises(ValidationError) as excinfo:
        services.set_preference(user=user, category="PASSWORD_CHANGED", channel=EMAIL, enabled=False)
    assert excinfo.value.code == 9161
    with pytest.raises(ValidationError) as excinfo:
        services.set_preference(user=user, category="NOPE", channel=EMAIL, enabled=False)
    assert excinfo.value.code == 9162
    with pytest.raises(ValidationError):
        services.set_preference(user=user, category="GENERIC", channel="FAX", enabled=False)

    matrix = {(row["category"], row["channel"]): row for row in selectors.preference_matrix(user)}
    assert matrix[("GENERIC", EMAIL)]["enabled"] is False
    assert matrix[("GENERIC", SMS)]["enabled"] is True
    assert matrix[("PASSWORD_CHANGED", EMAIL)]["mandatory"] is True

    services.set_preference(user=user, category="GENERIC", channel=EMAIL, enabled=True)
    assert selectors.wants(user, "GENERIC", EMAIL)


def test_failed_messages_are_retried_within_the_horizon(user, mailbox):
    from datetime import timedelta

    from django.utils import timezone

    row, _ = services.send_now(channel=EMAIL, subject="Hello", message="x")
    Notification.objects.filter(pk=row.pk).update(
        email_to=user.email, created_at=timezone.now() - timedelta(minutes=10)
    )
    assert tasks.resend_failed_notifications() == 1
    row.refresh_from_db()
    assert row.is_sent and row.attempts == 2

    Notification.objects.filter(pk=row.pk).update(is_sent=False, status=Status.FAILED.value, attempts=5)
    assert tasks.resend_failed_notifications() == 0
    assert tasks.send_notification_task(str(row.uid)) is False  # past the attempt cap


def test_resend_and_mark_read(user, mailbox):
    inapp, _ = services.send_now(
        channel=INAPP, category="GENERIC", title="Notice", message="x", receiver=user
    )
    assert inapp.is_sent and selectors.unread_in_app_count(user) == 1
    with pytest.raises(ValidationError):
        services.resend(uid=inapp.uid)
    with pytest.raises(ValidationError):
        services.mark_inapp_read(user=user)
    assert services.mark_inapp_read(user=user, uid=inapp.uid) == 1
    assert services.mark_inapp_read(user=user, read_all=True) == 0

    email, _ = services.send_now(channel=EMAIL, subject="Hello", message="x", receiver=user)
    assert services.resend(uid=email.uid).attempts == 2


# ── SMS ──────────────────────────────────────────────────────────────────────
def test_the_log_provider_only_delivers_where_allowed(user, settings):
    row, ok = services.send_now(channel=SMS, message="Hello", receiver=user)
    assert ok and "logged" in row.remarks

    settings.SMS_ALLOW_LOG_PROVIDER = False
    row, ok = services.send_now(channel=SMS, message="Hello", receiver=user)
    assert not ok and row.remarks == sms_backend.NO_GATEWAY_REMARK


def test_long_messages_are_clamped_and_numbers_normalised():
    assert len(sms_backend.clamp_sms("x" * 500)) == sms_backend.SMS_MAX_CHARS
    assert sms_backend.deliverable_phone("0755 123 456") == "+255755123456"
    assert sms_backend.deliverable_phone("12-34") == "1234"
    assert sms_backend.deliverable_phone("") == ""
    assert sms_backend.send_with_config("", "x", None) == (False, "No recipient phone number")


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code, self._body, self.text = status_code, body, text or str(body)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def gateway_row(provider="MGOV", **overrides):
    fields = {
        "api_url": "https://gateway.example/send",
        "sender_id": "INFO",
        "system_id": "SYS",
        "service_code": "SVC",
        "api_key": "k3y",
    }
    fields.update(overrides)
    return services.create_sms_configuration(name="Gateway", provider=provider, is_default=True, **fields)


def test_the_mgov_request_is_signed_over_the_exact_body(monkeypatch):
    import base64
    import hashlib
    import hmac

    sent = {}

    def fake_post(url, data=None, headers=None, timeout=None, **kwargs):
        sent.update(url=url, data=data, headers=headers)
        return FakeResponse(body={"code": 48200})

    monkeypatch.setattr(sms_backend.requests, "post", fake_post)
    ok, remark = sms_backend.send_with_config("+255755123456", "Hello", gateway_row())

    assert ok and "mGov accepted" in remark
    assert sent["url"] == "https://gateway.example/send"
    assert '"recipients":"255755123456"' in sent["data"]
    expected = base64.b64encode(hmac.new(b"k3y", sent["data"].encode(), hashlib.sha256).digest()).decode()
    assert sent["headers"]["hash"] == expected and sent["headers"]["sysId"] == "SYS"


@pytest.mark.parametrize(
    "response,expected",
    [
        (FakeResponse(body={"code": 48206}), "Insufficient SMS account balance (mGov 48206)"),
        (FakeResponse(body={"code": 99999}, text="odd"), "SMS gateway error 99999"),
        (FakeResponse(status_code=502, text="Bad gateway"), "HTTP 502"),
    ],
)
def test_gateway_refusals_say_why(monkeypatch, response, expected):
    monkeypatch.setattr(sms_backend.requests, "post", lambda *a, **k: response)
    ok, remark = sms_backend.send_with_config("+255755123456", "Hello", gateway_row())
    assert not ok and expected in remark


def test_an_unreachable_gateway_is_a_failed_delivery(monkeypatch):
    def down(*args, **kwargs):
        raise sms_backend.requests.ConnectionError("no route")

    monkeypatch.setattr(sms_backend.requests, "post", down)
    for provider in ("MGOV", "HTTP_JSON"):
        ok, remark = sms_backend.send_with_config("+255755123456", "Hello", gateway_row(provider))
        assert not ok and "Gateway unreachable" in remark
        SmsConfigurationCleanup()


def SmsConfigurationCleanup():
    from apps.notifications.models import SmsConfiguration

    SmsConfiguration.objects.all().delete()


def test_a_blank_setting_falls_back_to_the_environment(settings):
    settings.SMS_API_URL, settings.SMS_API_KEY = "https://env.example/send", "env-key"
    row = gateway_row(api_url="", api_key="", sender_id="")
    gateway = sms_backend.resolve_gateway(row)
    assert gateway.api_url == "https://env.example/send" and gateway.api_key == "env-key"
    assert set(gateway.from_env) == {"api_url", "api_key"}
    assert gateway.missing == ["sender_id"] and not gateway.ready
    assert sms_backend.send_with_config("+255755123456", "x", row)[1].startswith("SMS gateway not configured")
    assert sms_backend.provider_for(None) == "MGOV"
    settings.SMS_API_URL = ""
    assert sms_backend.provider_for(None) == "LOG"


def test_the_generic_gateway_sends_a_bearer_token(monkeypatch):
    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        sent.update(json=json, headers=headers)
        return FakeResponse(status_code=202, body={})

    monkeypatch.setattr(sms_backend.requests, "post", fake_post)
    ok, _ = sms_backend.send_with_config("+255755123456", "Hello", gateway_row("HTTP_JSON"))
    assert ok and sent["headers"]["Authorization"] == "Bearer k3y"
    assert sent["json"] == {"to": "+255755123456", "message": "Hello", "sender": "INFO"}


def test_a_project_can_register_its_own_provider():
    sms_backend.register_provider("ACME", "Acme SMS", lambda phone, message, config: (True, f"acme:{phone}"))
    try:
        row = services.create_sms_configuration(name="Acme", provider="acme")
        assert sms_backend.send_with_config("+255755123456", "x", row) == (True, "acme:+255755123456")
        with pytest.raises(ValidationError) as excinfo:
            services.create_sms_configuration(name="Nope", provider="carrier-pigeon")
        assert excinfo.value.code == 9163
    finally:
        sms_backend.PROVIDERS.pop("ACME"), sms_backend.PROVIDER_LABELS.pop("ACME")


def test_a_test_message_lands_in_the_log(admin, mailbox):
    row = services.create_sms_configuration(name="Log", provider="LOG")
    notification, ok = services.send_test_sms(uid=row.uid, phone="0755123456", actor=admin)
    assert ok and notification.phone_to == "+255755123456" and notification.category == "GENERIC"
    with pytest.raises(ValidationError):
        services.send_test_sms(uid=row.uid, phone="")

    server = services.create_email_configuration(name="Relay", host="smtp.example.org", password="p4ss")
    notification, ok = services.send_test_email(uid=server.uid, to_email="ops@example.org", actor=admin)
    assert notification.email_to == "ops@example.org" and notification.attempts == 1
    with pytest.raises(ValidationError):
        services.send_test_email(uid=server.uid, to_email="")


def test_configuration_secrets_are_encrypted_and_one_row_is_the_default():
    first = services.create_email_configuration(name="A", host="a.example", password="p4ss", is_default=True)
    second = services.create_email_configuration(name="B", host="b.example", is_default=True)
    first.refresh_from_db()
    assert not first.is_default and second.is_default
    assert first.password_encrypted != "p4ss" and first.get_password() == "p4ss"
    assert first.sender == "" or "@" in first.sender or first.sender == first.username

    services.update_email_configuration(uid=first.uid, password="n3w", is_default=True, from_name="Desk")
    first.refresh_from_db()
    assert first.get_password() == "n3w" and first.is_default
    with pytest.raises(ValidationError):
        services.create_email_configuration(name="", host="")
    with pytest.raises(ValidationError):
        services.create_email_configuration(name="C", host="c.example", use_tls=True, use_ssl=True)
    services.delete_email_configuration(uid=second.uid)

    gateway = gateway_row()
    assert gateway.api_key_encrypted != "k3y" and gateway.get_api_key() == "k3y"
    services.update_sms_configuration(uid=gateway.uid, sender_id="", api_key="rotated", is_default=True)
    gateway.refresh_from_db()
    assert gateway.sender_id == "" and gateway.get_api_key() == "rotated"
    with pytest.raises(ValidationError):
        services.update_sms_configuration(uid=gateway.uid, name=" ")
    services.delete_sms_configuration(uid=gateway.uid)
