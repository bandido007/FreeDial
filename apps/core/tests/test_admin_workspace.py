"""All staff roles share the workspace shell; object editors preserve Django permissions."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from apps.agents.models import Agent

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client():
    user = get_user_model().objects.create_superuser(email="ui-admin@example.org", password="test-only")
    client = Client()
    client.force_login(user)
    return client


def test_admin_editor_has_a_searchable_record_list_beside_the_existing_form(staff_client):
    first = Agent.objects.create(number="501", name="Juma")
    Agent.objects.create(number="502", name="Neema")
    page = staff_client.get(f"/admin/agents/agent/{first.pk}/change/")
    assert page.status_code == 200
    html = page.content.decode()
    assert 'id="workspace-records"' in html and 'name="workspace_q"' in html
    assert "Neema" in html and 'name="csrfmiddlewaretoken"' in html
    assert 'name="number"' in html and "algodial/admin.css" in html


def test_admin_object_list_opens_the_same_editor_pattern(staff_client):
    Agent.objects.create(number="501", name="Juma")
    Agent.objects.create(number="502", name="Neema")
    page = staff_client.get("/admin/agents/agent/?workspace_q=Juma")
    assert page.status_code == 200
    assert [str(row["label"]) for row in page.context["workspace_records"]] == ["501 Juma"]
    assert "Select a record" in page.content.decode()


def test_staff_without_permissions_cannot_read_the_new_sidebar(make_user):
    user = make_user(is_staff=True)
    client = Client()
    client.force_login(user)
    assert client.get("/admin/agents/agent/").status_code == 403


def test_login_reports_and_read_only_pages_use_the_shared_theme(staff_client):
    assert "algodial/admin.css" in Client().get("/admin/login/").content.decode()
    for path in ("/admin/", "/admin/reports/report/", "/admin/calls/event/"):
        page = staff_client.get(path)
        assert page.status_code == 200
        assert "algodial/admin.css" in page.content.decode()


def test_settings_editor_validates_unsaved_defaults_and_resets_overrides(staff_client):
    from apps.configuration import services
    from apps.configuration.models import SystemSetting

    path = "/admin/configuration/systemsetting/?key=SUPERVISOR_LONG_CALL_SECONDS"
    page = staff_client.get(path)
    assert page.status_code == 200
    assert page.context["setting_form"].initial["value"] == 600
    assert not SystemSetting.objects.filter(key="SUPERVISOR_LONG_CALL_SECONDS").exists()
    invalid = staff_client.post(path, {"value": "-1"})
    assert invalid.status_code == 200 and invalid.context["setting_form"].errors
    assert staff_client.post(path, {"value": "900"}).status_code == 302
    assert services.value_of("SUPERVISOR_LONG_CALL_SECONDS") == 900
    assert staff_client.post(path, {"action": "reset"}).status_code == 302
    assert services.value_of("SUPERVISOR_LONG_CALL_SECONDS") == 600
