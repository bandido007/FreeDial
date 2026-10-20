"""The Angular workspace preserves the permissions and save policy of the existing pages."""

import io
import json
from pathlib import Path

import pytest
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import Client
from oauth2_provider.models import get_access_token_model

from apps.agents.models import Agent, Skill
from apps.queues.models import SkillRuleSet

pytestmark = pytest.mark.django_db


@pytest.fixture
def operator(make_user):
    return make_user(is_staff=True, is_superuser=True, account_type="System")


def test_public_config_exposes_only_the_public_client_id(oauth_app):
    reply = Client().get("/workspace/config")
    assert reply.status_code == 200
    assert reply.json() == {"clientId": oauth_app.client_id}


def test_workspace_refuses_cookie_only_identity_tokens_and_non_staff(api, operator, make_user):
    cookie = Client()
    cookie.force_login(operator)
    assert cookie.get("/workspace/catalogue").status_code in (401, 403)
    client = api(operator)
    token = get_access_token_model().objects.get(token=client.access_token)
    token.scope = "openid profile email"
    token.save(update_fields=["scope"])
    assert client.get("/workspace/catalogue").status_code == 403
    assert api(make_user()).get("/workspace/catalogue").status_code == 403


def test_catalogue_contains_only_existing_management_pages(api, operator):
    reply = api(operator).get("/workspace/catalogue")
    assert reply.status_code == 200
    labels = {row["key"] for row in reply.json()}
    assert {"agents.agent", "queues.queue", "telephony.trunk", "setup.setupconfirmation"} <= labels
    assert not any("workflow" in label or "clientconnection" in label for label in labels)


def test_role_permissions_guard_records_and_related_choices(api, make_user, grant):
    user = make_user(is_staff=True)
    grant(user, "can_view_queues")
    client = api(user)
    assert client.get("/workspace/records/agents.agent").status_code == 403
    assert (
        client.post(
            "/workspace/records/queues.queue/new",
            data=json.dumps({"values": {}}),
            content_type="application/json",
        ).status_code
        == 403
    )
    assert client.get("/workspace/records/queues.queue").status_code == 200


def test_native_validation_and_service_saves_are_used_for_agents(api, operator):
    client = api(operator)
    # No inlines on a new record: management counts are mandatory, as in Django.
    values = {
        "number": "abc",
        "name": "Juma",
        "active": True,
        "skills-TOTAL_FORMS": "0",
        "skills-INITIAL_FORMS": "0",
        "skills-MIN_NUM_FORMS": "0",
        "skills-MAX_NUM_FORMS": "1000",
    }
    reply = client.post(
        "/workspace/records/agents.agent/new",
        data=json.dumps({"values": values}),
        content_type="application/json",
    )
    assert reply.status_code == 422, reply.content
    assert not Agent.objects.exists()
    values["number"] = "5101"
    reply = client.post(
        "/workspace/records/agents.agent/new",
        data=json.dumps({"values": values}),
        content_type="application/json",
    )
    assert reply.status_code == 200, reply.content
    agent = Agent.objects.get(number="5101")
    assert str(agent.pk) == reply.json()["id"] and agent.name == "Juma"
    assert agent.created_by == operator
    assert client.get(f"/workspace/records/agents.agent/{agent.pk}").status_code == 200


def test_every_existing_record_form_serializes_without_password_hashes(api, operator):
    call_command("seed_demo", password="test-demo-only", stdout=io.StringIO())
    client = api(operator)
    for entry in client.get("/workspace/catalogue").json():
        if entry["kind"] != "records":
            continue
        rows = client.get("/workspace/records/" + entry["key"])
        assert rows.status_code == 200, (entry, rows.content)
        ids = [row["id"] for row in rows.json()["rows"][:1]]
        if entry["canAdd"]:
            ids.append("new")
        for pk in ids:
            reply = client.get(f"/workspace/records/{entry['key']}/{pk}")
            assert reply.status_code == 200, (entry, reply.content)
            body = reply.content.decode()
            assert "md5$" not in body and "pbkdf2_sha256$" not in body
            assert "secret_encrypted" not in body and "password_encrypted" not in body


def test_invalid_inline_rules_do_not_partially_save_the_parent(api, operator):
    Skill.objects.create(name="english", kind="language")
    rules = SkillRuleSet.objects.create(name="Original")
    values = {
        "name": "Changed",
        "active": True,
        "requirements-TOTAL_FORMS": "1",
        "requirements-INITIAL_FORMS": "0",
        "requirements-MIN_NUM_FORMS": "0",
        "requirements-MAX_NUM_FORMS": "1000",
        "requirements-0-skill": "$language",
        "requirements-0-min_level": "3",
        "requirements-0-until_seconds": "",
    }
    reply = api(operator).post(
        f"/workspace/records/queues.skillruleset/{rules.pk}",
        data=json.dumps({"values": values}),
        content_type="application/json",
    )
    assert reply.status_code == 422, reply.content
    rules.refresh_from_db()
    assert rules.name == "Original" and not rules.requirements.exists()


def test_writer_owned_calls_cannot_be_created_or_modified(api, operator):
    client = api(operator)
    assert (
        client.post(
            "/workspace/records/calls.call/new",
            data=json.dumps({"values": {}}),
            content_type="application/json",
        ).status_code
        == 403
    )


def test_restricted_sessions_cannot_use_the_workspace(api, operator):
    client = api(operator)
    operator.must_change_password = True
    operator.save(update_fields=["must_change_password"])
    assert client.get("/workspace/catalogue").status_code == 403


def test_settings_keep_native_permissions_constraints_and_reset(api, operator, make_user):
    viewer = make_user(is_staff=True)
    viewer.user_permissions.add(Permission.objects.get(codename="view_systemsetting"))
    readonly = api(viewer)
    assert readonly.get("/workspace/settings").json()["canChange"] is False
    row = next(
        row
        for row in api(operator).get("/workspace/settings").json()["rows"]
        if row["type"] == "INTEGER" and row["minimum"] is not None
    )
    body = {"key": row["key"], "value": row["minimum"]}
    assert (
        readonly.post(
            "/workspace/settings", data=json.dumps(body), content_type="application/json"
        ).status_code
        == 403
    )
    client = api(operator)
    bad = client.post(
        "/workspace/settings",
        data=json.dumps({**body, "value": row["minimum"] - 1}),
        content_type="application/json",
    )
    assert bad.status_code == 400
    assert (
        client.post("/workspace/settings", data=json.dumps(body), content_type="application/json").status_code
        == 200
    )
    reset = client.post(
        "/workspace/settings",
        data=json.dumps({"key": row["key"], "action": "reset"}),
        content_type="application/json",
    )
    assert reset.status_code == 200 and reset.json()["is_changed"] is False


def test_password_changes_use_native_validation_and_history_permissions(api, operator, make_user):
    user = make_user(is_staff=True)
    path = f"/workspace/user-password/{user.pk}"
    values = {"usable_password": "true", "password1": "Example-new-pass-482!", "password2": "does-not-match"}
    client = api(operator)
    assert api(user).post(path, data=json.dumps(values), content_type="application/json").status_code == 403
    assert client.post(path, data=json.dumps(values), content_type="application/json").status_code == 422
    values["password2"] = values["password1"]
    assert client.post(path, data=json.dumps(values), content_type="application/json").status_code == 200
    user.refresh_from_db()
    assert user.check_password(values["password1"])
    history = f"/workspace/history/users.user/{user.pk}"
    assert api(user, password=values["password1"]).get(history).status_code == 403
    assert "Password changed" in client.get(history).json()[0]["action"]


def test_malformed_report_dates_are_validation_errors(api, operator):
    assert api(operator).get("/workspace/reports?period=custom&since=not-a-date").status_code == 400


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[3] / "frontend/dist/algodial/browser/index.html").exists(),
    reason="Build the Angular frontend first.",
)
def test_compiled_spa_uses_a_nonce_and_valid_deep_routes():
    client = Client()
    reply = client.get("/app/manage/agents.agent")
    assert reply.status_code == 200
    assert 'ngCspNonce="' in reply.content.decode()
    assert "base-uri 'self'" in reply["Content-Security-Policy"]
    assert "'unsafe-inline'" not in reply["Content-Security-Policy"]
    assert client.get("/app/no-such-asset.js").status_code == 404
    assert client.get("/app/../../package.json").status_code == 404
