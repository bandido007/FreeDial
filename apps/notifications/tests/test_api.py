"""The GraphQL surface of notifications."""

import pytest

from apps.notifications import services

pytestmark = pytest.mark.django_db

PAGE = {"searchDto": {"pageableParam": {"pageNumber": 1, "itemsPerPage": 20}}}


def test_my_feed_my_count_and_mark_read(gql, user, make_user):
    services.send_now(channel="INAPP", category="GENERIC", title="Mine", message="x", receiver=user)
    services.send_now(channel="INAPP", category="GENERIC", title="Theirs", message="x", receiver=make_user())

    feed = gql("query { getMyInAppNotifications { dataList { uid title isRead categoryName } } }", user=user)
    assert [row["title"] for row in feed["dataList"]] == ["Mine"]
    assert feed["dataList"][0]["categoryName"] == "General message"
    assert gql("query { getMyUnreadNotificationCount { data { unread } } }", user=user)["data"]["unread"] == 1

    paged = gql(
        "query Q($searchDto: SearchDataDto!) { getMyInAppNotificationsPageable(searchDto: $searchDto, "
        "unreadOnly: true) { content { title } page { totalElements } } }",
        PAGE,
        user=user,
    )
    assert paged["page"]["totalElements"] == 1

    done = gql("mutation { markInAppNotificationRead(readAll: true) { code message } }", user=user)
    assert done["message"] == "1 notification(s) marked as read."
    assert gql("query { getMyNotifications { dataList { title } } }", user=user)["dataList"]
    assert gql("query { getMyInAppNotifications { code } }")["code"] == 9000


def test_preferences(gql, user):
    matrix = gql(
        "query { getMyNotificationPreferences { dataList { category channel enabled mandatory } } }",
        user=user,
    )
    assert all(row["enabled"] for row in matrix["dataList"])

    change = """
    mutation M($input: NotificationPreferenceInput!) {
      setMyNotificationPreference(input: $input) { code message dataList { category channel enabled } }
    }"""
    muted = gql(change, {"input": {"category": "GENERIC", "channel": "EMAIL", "enabled": False}}, user=user)
    row = next(r for r in muted["dataList"] if (r["category"], r["channel"]) == ("GENERIC", "EMAIL"))
    assert row["enabled"] is False
    refused = gql(
        change, {"input": {"category": "PASSWORD_CHANGED", "channel": "EMAIL", "enabled": False}}, user=user
    )
    assert refused["code"] == 9161


def test_the_log_is_for_those_allowed_to_see_it(gql, user, admin, mailbox):
    services.send_now(channel="EMAIL", category="GENERIC", subject="Hello", message="x", receiver=user)
    query = """
    query Q($searchDto: SearchDataDto!, $filtering: NotificationFilterInput) {
      getNotificationsPageable(searchDto: $searchDto, filtering: $filtering) {
        code content { uid channel status subject receiverName receiverUid } page { totalElements }
      }
    }"""
    assert gql(query, PAGE, user=user)["code"] == 9000
    found = gql(query, {**PAGE, "filtering": {"channel": "EMAIL", "search": "hello"}}, user=admin)
    assert found["page"]["totalElements"] == 1 and found["content"][0]["status"] == "SENT"
    assert gql(query, {**PAGE, "filtering": {"channel": "SMS"}}, user=admin)["page"]["totalElements"] == 0
    future = {**PAGE, "filtering": {"dateFrom": "2999-01-01T00:00:00"}}
    assert gql(query, future, user=admin)["code"] == 9002

    uid = found["content"][0]["uid"]
    resent = gql(
        "mutation R($uid: UUID!) { resendNotification(uid: $uid) { code message data { attempts } } }",
        {"uid": uid},
        user=admin,
    )
    assert resent["data"]["attempts"] == 2


def test_configuration_never_returns_a_secret(gql, admin, user):
    created = gql(
        """
        mutation C($input: EmailConfigurationInput!) {
          createEmailConfiguration(input: $input) { code data { uid name hasPassword verifyTls isDefault } }
        }""",
        {"input": {"name": "Relay", "host": "smtp.example.org", "password": "p4ss", "isDefault": True}},
        user=admin,
    )
    assert created["data"] == {
        "uid": created["data"]["uid"],
        "name": "Relay",
        "hasPassword": True,
        "verifyTls": True,
        "isDefault": True,
    }
    from config.schema import schema

    email_type = str(schema).split("type EmailConfigurationType")[1].split("}")[0]
    assert "password" not in email_type.replace("hasPassword", "")

    listed = gql("query { getEmailConfigurations { code dataList { name } } }", user=admin)
    assert [row["name"] for row in listed["dataList"]] == ["Relay"]
    assert gql("query { getEmailConfigurations { code } }", user=user)["code"] == 9000

    updated = gql(
        "mutation U($input: EmailConfigurationInput!) { updateEmailConfiguration(input: $input) "
        "{ code data { verifyTls } } }",
        {"input": {"uid": created["data"]["uid"], "verifyTls": False}},
        user=admin,
    )
    assert updated["data"]["verifyTls"] is False
    tested = gql(
        'mutation T($uid: UUID!) { testEmailConfiguration(uid: $uid, email: "ops@example.org") { code } }',
        {"uid": created["data"]["uid"]},
        user=admin,
    )
    assert tested["code"] in (9001, 9004)
    gone = gql(
        "mutation D($uid: UUID!) { deleteEmailConfiguration(uid: $uid) { code } }",
        {"uid": created["data"]["uid"]},
        user=admin,
    )
    assert gone["code"] == 9001


def test_sms_gateways_templates_and_categories(gql, admin):
    providers = gql("query { getSmsProviders { dataList { key name requiredSettings } } }", user=admin)
    assert {row["key"] for row in providers["dataList"]} >= {"LOG", "MGOV", "HTTP_JSON"}

    created = gql(
        """
        mutation C($input: SmsConfigurationInput!) {
          createSmsConfiguration(input: $input) {
            code data { uid provider hasApiKey isReady missingSettings envFallbacks }
          }
        }""",
        {"input": {"name": "Gateway", "provider": "MGOV", "apiUrl": "https://g.example", "apiKey": "k"}},
        user=admin,
    )
    data = created["data"]
    assert data["hasApiKey"] and not data["isReady"]
    assert set(data["missingSettings"]) == {"sender_id", "system_id", "service_code"}

    switched = gql(
        "mutation U($input: SmsConfigurationInput!) { updateSmsConfiguration(input: $input) "
        "{ code data { provider isReady } } }",
        {"input": {"uid": data["uid"], "provider": "LOG"}},
        user=admin,
    )
    assert switched["data"] == {"provider": "LOG", "isReady": True}
    sent = gql(
        'mutation T($uid: UUID!) { testSmsConfiguration(uid: $uid, phone: "0755123456") { code message } }',
        {"uid": data["uid"]},
        user=admin,
    )
    assert sent["code"] == 9001
    assert gql("query { getSmsConfigurations { dataList { name } } }", user=admin)["dataList"]
    gql(
        "mutation D($uid: UUID!) { deleteSmsConfiguration(uid: $uid) { code } }",
        {"uid": data["uid"]},
        user=admin,
    )

    template = gql(
        """
        mutation C($input: NotificationTemplateInput!) {
          createNotificationTemplate(input: $input) { code data { uid category categoryName channel } }
        }""",
        {"input": {"name": "Greeting", "category": "GENERIC", "channel": "EMAIL", "body": "Hello"}},
        user=admin,
    )
    assert template["data"]["categoryName"] == "General message"
    uid = template["data"]["uid"]
    gql(
        "mutation U($input: NotificationTemplateInput!) { updateNotificationTemplate(input: $input) { code } }",
        {"input": {"uid": uid, "body": "Hello again"}},
        user=admin,
    )
    assert gql("query { getNotificationTemplates { dataList { body } } }", user=admin)["dataList"] == [
        {"body": "Hello again"}
    ]
    gql(
        "mutation D($uid: UUID!) { deleteNotificationTemplate(uid: $uid) { code } }", {"uid": uid}, user=admin
    )

    categories = gql(
        "query { getNotificationCategories { dataList { code mandatory sensitive } } }", user=admin
    )
    codes = {row["code"]: row for row in categories["dataList"]}
    assert codes["OTP_VERIFICATION"] == {"code": "OTP_VERIFICATION", "mandatory": True, "sensitive": True}
