"""The Add agent form suggests the next free agent number from a range set in System settings, so the
admin does not have to guess; any number can still be typed, and a taken one is still refused."""

import pytest

from apps.agents import services
from apps.agents.models import Agent
from apps.core.exceptions import DuplicateError

pytestmark = pytest.mark.django_db


@pytest.fixture
def numbers(settings):
    settings.AGENT_NUMBER_FIRST, settings.AGENT_NUMBER_LAST = 2000, 2003
    return settings


def test_the_next_free_number_skips_every_number_ever_used(numbers):
    assert services.next_free_number() == "2000"
    services.create_agent(actor=None, number="2000", name="Amina")
    gone = services.create_agent(actor=None, number="2001", name="Left")
    services.delete_agent(uid=gone.uid)
    assert services.next_free_number() == "2002", "a deleted agent's number would share its old browser phone"
    services.create_agent(actor=None, number="9999", name="Outside the range")
    assert services.next_free_number() == "2002"


def test_a_full_range_suggests_nothing(numbers):
    for number in ("2000", "2001", "2002", "2003"):
        services.create_agent(actor=None, number=number, name=f"Agent {number}")
    assert services.next_free_number() is None


def test_a_taken_number_is_still_refused(numbers):
    services.create_agent(actor=None, number="2000", name="Amina")
    with pytest.raises(DuplicateError):
        services.create_agent(actor=None, number="2000", name="Again")
    assert Agent.objects.count() == 1


def test_the_add_form_is_filled_with_the_suggestion(numbers, client, make_user, grant):
    manager = make_user(account_type="Internal", is_staff=True)
    grant(manager, "can_view_agents", "can_manage_agents")
    client.force_login(manager)
    services.create_agent(actor=None, number="2000", name="Amina")
    html = client.get("/admin/agents/agent/add/").content.decode()
    assert 'name="number" value="2001"' in html
    assert "next free number from 2000 to 2003" in html
