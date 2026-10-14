"""M1: a SIP account saved in AlgoDial is a PJSIP endpoint Asterisk can read at once.

The rows live in Asterisk's realtime tables (ps_endpoints, ps_auths, ps_aors),
which Asterisk's Alembic creates. They exist exactly while the account is active
and not deleted, and they never hold the plain password.
"""

import hashlib

import pytest
from django.db import connection
from django.test import Client

from apps.core.crypto import decrypt_secret
from apps.core.exceptions import DuplicateError, NotFoundError, ValidationError
from apps.telephony import asterisk_schema, services
from apps.telephony.models import PsAor, PsAuth, PsEndpoint, SipAccount

pytestmark = pytest.mark.django_db

PASSWORD = "Long-enough-secret-1"
PAGE = {"searchDto": {"pageableParam": {"pageNumber": 1, "itemsPerPage": 20}}}


@pytest.fixture
def manager(make_user, grant):
    person = make_user(account_type="Internal")
    grant(person, "can_view_sip_accounts", "can_manage_sip_accounts")
    return person


def md5_digest(username, realm, password):
    return "MD5:" + hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()  # noqa: S324


def pjsip_rows(sip_id):
    return (
        PsEndpoint.objects.filter(id=sip_id).first(),
        PsAuth.objects.filter(id=sip_id).first(),
        PsAor.objects.filter(id=sip_id).first(),
    )


def test_the_realtime_tables_come_from_asterisk_alembic():
    with connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM alembic_version_config")
        assert cursor.fetchone()[0] == asterisk_schema.head_revision()


def test_creating_an_account_writes_endpoint_auth_and_aor(manager, settings):
    account = services.create_sip_account(
        actor=manager, sip_id="1003", kind="SOFTPHONE", password=PASSWORD, display_name="Test desk"
    )
    endpoint, auth, aor = pjsip_rows("1003")

    assert endpoint.aors == "1003" and endpoint.auth == "1003"
    assert endpoint.context == settings.PJSIP_DEFAULT_CONTEXT
    assert endpoint.transport == settings.PJSIP_TRANSPORT_SOFTPHONE
    assert endpoint.disallow == "all" and endpoint.allow == "ulaw,alaw"
    assert endpoint.callerid == '"Test desk" <1003>'
    assert endpoint.direct_media == "no" and endpoint.webrtc in (None, "no")

    assert auth.username == "1003" and auth.realm == settings.PJSIP_REALM
    assert auth.password_digest == md5_digest("1003", settings.PJSIP_REALM, PASSWORD)
    assert aor.max_contacts == 1 and aor.remove_existing == "yes"
    assert account.created_by == manager


def test_the_plain_password_is_stored_nowhere(manager):
    account = services.create_sip_account(actor=manager, sip_id="1003", kind="SOFTPHONE", password=PASSWORD)
    assert PASSWORD not in account.password_encrypted
    assert decrypt_secret(account.password_encrypted) == PASSWORD
    _, auth, _ = pjsip_rows("1003")
    assert auth.password is None and auth.md5_cred is None


def test_a_browser_account_is_a_webrtc_endpoint(manager, settings):
    services.create_sip_account(actor=manager, sip_id="web-01", kind="BROWSER", password=PASSWORD)
    endpoint, _, _ = pjsip_rows("web-01")
    assert endpoint.webrtc == "yes"
    assert endpoint.transport == settings.PJSIP_TRANSPORT_BROWSER
    assert endpoint.allow == "opus,ulaw"


def test_changing_the_password_changes_the_digest(manager, settings):
    account = services.create_sip_account(actor=manager, sip_id="1003", kind="SOFTPHONE", password=PASSWORD)
    services.update_sip_account(uid=account.uid, actor=manager, password="Another-long-secret-2")
    _, auth, _ = pjsip_rows("1003")
    assert auth.password_digest == md5_digest("1003", settings.PJSIP_REALM, "Another-long-secret-2")


def test_rows_exist_only_while_the_account_is_active(manager):
    account = services.create_sip_account(actor=manager, sip_id="1003", kind="SOFTPHONE", password=PASSWORD)
    services.set_sip_account_active(uid=account.uid, actor=manager, active=False)
    assert pjsip_rows("1003") == (None, None, None)
    services.set_sip_account_active(uid=account.uid, actor=manager, active=True)
    assert all(pjsip_rows("1003"))


def test_deleting_removes_the_rows_and_frees_the_id(manager):
    account = services.create_sip_account(actor=manager, sip_id="1003", kind="SOFTPHONE", password=PASSWORD)
    services.delete_sip_account(uid=account.uid)
    assert pjsip_rows("1003") == (None, None, None)
    assert SipAccount.all_objects.get(uid=account.uid).deleted  # kept for the trail
    services.create_sip_account(actor=manager, sip_id="1003", kind="SOFTPHONE", password=PASSWORD)
    assert all(pjsip_rows("1003"))


def test_a_hard_delete_also_removes_the_rows(manager):
    account = services.create_sip_account(actor=manager, sip_id="1003", kind="SOFTPHONE", password=PASSWORD)
    SipAccount.objects.filter(pk=account.pk).delete()  # what a bulk delete does
    assert pjsip_rows("1003") == (None, None, None)


@pytest.mark.parametrize("sip_id", ["", "x", "has space", "semi;colon", "a" * 41])
def test_a_bad_sip_id_is_refused(manager, sip_id):
    with pytest.raises(ValidationError):
        services.create_sip_account(actor=manager, sip_id=sip_id, kind="SOFTPHONE", password=PASSWORD)


def test_a_taken_sip_id_is_refused(manager):
    services.create_sip_account(actor=manager, sip_id="1003", kind="SOFTPHONE", password=PASSWORD)
    with pytest.raises(DuplicateError):
        services.create_sip_account(actor=manager, sip_id="1003", kind="SOFTPHONE", password=PASSWORD)


def test_a_short_password_is_refused(manager):
    with pytest.raises(ValidationError):
        services.create_sip_account(actor=manager, sip_id="1003", kind="SOFTPHONE", password="short")


def test_no_password_means_a_generated_one(manager):
    account = services.create_sip_account(actor=manager, sip_id="1003", kind="SOFTPHONE")
    assert len(decrypt_secret(account.password_encrypted)) >= services.MIN_PASSWORD_LENGTH


def test_a_failed_asterisk_write_leaves_nothing_behind(manager, monkeypatch):
    from apps.telephony import pjsip

    def broken(account):
        raise RuntimeError("database said no")

    monkeypatch.setattr(pjsip, "write", broken)
    with pytest.raises(RuntimeError):
        services.create_sip_account(actor=manager, sip_id="1003", kind="SOFTPHONE", password=PASSWORD)
    assert not SipAccount.all_objects.filter(sip_id="1003").exists()


def test_resync_rebuilds_every_row(manager):
    services.create_sip_account(actor=manager, sip_id="1003", kind="SOFTPHONE", password=PASSWORD)
    PsEndpoint.objects.all().delete()
    PsAuth.objects.all().delete()
    PsAor.objects.all().delete()
    assert services.resync_pjsip() == 1
    assert all(pjsip_rows("1003"))


def test_the_api(gql, manager, user):
    saved = gql(
        "mutation S($input: SipAccountInput!) { saveSipAccount(input: $input) { code data { uid sipId kind } } }",
        {"input": {"sipId": "1003", "kind": "SOFTPHONE", "password": PASSWORD}},
        user=manager,
    )
    assert saved["code"] == 9001 and saved["data"]["sipId"] == "1003"
    uid = saved["data"]["uid"]
    refused = gql(
        "mutation S($input: SipAccountInput!) { saveSipAccount(input: $input) { code } }",
        {"input": {"sipId": "1004", "kind": "SOFTPHONE"}},
        user=user,
    )
    assert refused["code"] == 9000

    page = gql(
        "query L($searchDto: SearchDataDto!, $filtering: SipAccountFilterInput) {"
        " getSipAccountsPageable(searchDto: $searchDto, filtering: $filtering) { content { sipId } page { totalElements } } }",
        {**PAGE, "filtering": {"search": "100"}},
        user=manager,
    )
    assert page["page"]["totalElements"] == 1
    off = gql(
        "mutation A($uid: UUID!) { setSipAccountActive(uid: $uid, active: false) { data { active } } }",
        {"uid": uid},
        user=manager,
    )
    assert off["data"]["active"] is False
    assert (
        gql("mutation D($uid: UUID!) { deleteSipAccount(uid: $uid) { code } }", {"uid": uid}, user=manager)[
            "code"
        ]
        == 9001
    )
    with pytest.raises(NotFoundError):
        services.delete_sip_account(uid=uid)


def test_the_api_never_returns_a_password(gql):
    schema = gql('{ __type(name: "SipAccountType") { fields { name } } }', {})
    names = {field["name"] for field in schema["fields"]} if schema else set()
    assert names and not {name for name in names if "password" in name.lower()}


def test_the_admin_page_creates_and_deletes_an_account(admin, settings):
    """The Done-when of M1, minus the phone: the admin page writes what Asterisk reads."""
    browser = Client()
    browser.force_login(admin)

    response = browser.post(
        "/admin/telephony/sipaccount/add/",
        {
            "sip_id": "1003",
            "kind": "SOFTPHONE",
            "display_name": "Desk 3",
            "new_password": PASSWORD,
            "codecs": "",
            "context": "",
            "active": "on",
        },
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    _, auth, _ = pjsip_rows("1003")
    assert auth.password_digest == md5_digest("1003", settings.PJSIP_REALM, PASSWORD)

    account = SipAccount.objects.get(sip_id="1003")
    response = browser.post(
        "/admin/telephony/sipaccount/",
        {"action": "delete_selected", "_selected_action": [account.pk], "post": "yes"},
    )
    assert response.status_code == 302
    assert pjsip_rows("1003") == (None, None, None)
    assert SipAccount.all_objects.get(pk=account.pk).deleted
