"""Admin -> Setup guide: the checklist and the user manual on one page, for the people who set up."""

import pytest

from apps.setup.models import SetupConfirmation
from apps.setup.permissions import CONFIRM_SETUP_TESTS, VIEW_SETUP_GUIDE

pytestmark = pytest.mark.django_db

PAGE = "/admin/setup/setupconfirmation/"


@pytest.fixture(autouse=True)
def no_redis(monkeypatch):
    from apps.setup import checklist

    monkeypatch.setattr(checklist, "read_consumers", lambda: {"brain": 500, "writer": 500, "ai": 500})


def test_the_guide_follows_the_permissions(client, make_user, grant):
    stranger = make_user(account_type="Internal", is_staff=True)
    client.force_login(stranger)
    assert client.get(PAGE).status_code == 403

    reader = make_user(account_type="Internal", is_staff=True)
    grant(reader, VIEW_SETUP_GUIDE)
    client.force_login(reader)
    page = client.get(PAGE)
    assert page.status_code == 200
    html = page.content.decode()
    assert "Setup checklist" in html and "User manual" in html
    assert 'id="manual-agents"' in html and 'id="manual-troubleshooting"' in html
    assert f'action="{PAGE}confirm/"' not in html, "only CONFIRM_SETUP_TESTS may confirm"
    assert client.post(PAGE + "confirm/", {"check": "confirm.audio"}).status_code == 403


def test_a_manager_confirms_a_hands_on_test(client, make_user, grant):
    manager = make_user(account_type="Internal", is_staff=True, first_name="Tostao")
    grant(manager, VIEW_SETUP_GUIDE, CONFIRM_SETUP_TESTS)
    client.force_login(manager)
    assert f'action="{PAGE}confirm/"' in client.get(PAGE).content.decode()

    response = client.post(PAGE + "confirm/", {"check": "confirm.audio", "note": "Clear both ways"})
    assert response.status_code == 302 and response["Location"].endswith("#check-confirm.audio")
    row = SetupConfirmation.objects.get()
    assert (row.test, row.note, row.created_by) == ("confirm.audio", "Clear both ways", manager)
    assert "Confirmed by Tostao" in client.get(PAGE).content.decode()

    client.post(PAGE + "clear/", {"check": "confirm.audio"})
    assert not SetupConfirmation.objects.exists() and SetupConfirmation.all_objects.get().deleted


def test_the_header_links_to_the_guide(client, make_user, grant):
    reader = make_user(account_type="Internal", is_staff=True)
    grant(reader, VIEW_SETUP_GUIDE)
    client.force_login(reader)
    assert f'href="{PAGE}"' in client.get("/admin/").content.decode()
