"""Agents, queues and entry points: the configuration the brain decides with."""

import pytest
from django.conf import settings
from django.test import Client

from apps.agents import services as agents
from apps.calls import bus
from apps.core.exceptions import DuplicateError, ValidationError
from apps.queues import services as queues
from apps.queues.models import QueueMember
from contracts import messages as m
from contracts.envelope import decode

pytestmark = pytest.mark.django_db


@pytest.fixture
def commands(settings):
    settings.BUS_COMMANDS_STREAM = "test:queues:commands"
    client = bus.client()
    client.delete(settings.BUS_COMMANDS_STREAM)
    yield lambda: [
        decode(fields[bus.FIELD.encode()].decode())
        for _, fields in client.xrange(settings.BUS_COMMANDS_STREAM)
    ]
    client.delete(settings.BUS_COMMANDS_STREAM)


def test_agent_numbers_are_digits_and_unique_among_live_agents(user):
    agent = agents.create_agent(actor=None, number="7001", name="  Asha   M ", user=user)
    assert agent.name == "Asha M"
    with pytest.raises(DuplicateError):
        agents.create_agent(actor=None, number="7001", name="Other")
    with pytest.raises(ValidationError):
        agents.create_agent(actor=None, number="70a1", name="Other")
    with pytest.raises(DuplicateError):
        agents.create_agent(actor=None, number="7002", name="Other", user=user)
    agents.delete_agent(uid=agent.uid)
    again = agents.create_agent(
        actor=None, number="7001", name="Asha", user=user
    )  # number and user are free again
    assert again.pk != agent.pk


def test_queue_settings_are_checked_and_members_replaced():
    a = agents.create_agent(actor=None, number="7001", name="A")
    b = agents.create_agent(actor=None, number="7002", name="B")
    queue = queues.save_queue(actor=None, name="Support", ring_timeout=15, agents=[a, b])
    assert set(queue.agents.all()) == {a, b}
    queues.save_queue(actor=None, uid=queue.uid, name="Support", agents=[b])
    assert list(queue.agents.all()) == [b]
    with pytest.raises(ValidationError):
        queues.save_queue(actor=None, name="Sales", ring_timeout=1)
    with pytest.raises(DuplicateError):
        queues.save_queue(actor=None, name="support")


def test_an_entry_point_number_leads_to_one_queue():
    queue = queues.save_queue(actor=None, name="Support")
    queues.save_entry_point(actor=None, number="800", queue=queue)
    with pytest.raises(DuplicateError):
        queues.save_entry_point(actor=None, number="800", queue=queue)
    with pytest.raises(ValidationError):
        queues.save_entry_point(actor=None, number="8 00", queue=queue)


def test_a_change_tells_the_brain_once_per_transaction(commands, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        a = agents.create_agent(actor=None, number="7001", name="A")
        queue = queues.save_queue(actor=None, name="Support", agents=[a])
        queues.save_entry_point(actor=None, number="800", queue=queue)
    sent = commands()
    assert [command.type for command in sent] == [m.CONFIG_CHANGED]
    assert sent[0].command_id


def test_removing_members_tells_the_brain(monkeypatch):
    from apps.queues import receivers

    a = agents.create_agent(actor=None, number="7001", name="A")
    queue = queues.save_queue(actor=None, name="Support", agents=[a])
    calls = []
    monkeypatch.setattr(receivers, "config_changed", lambda: calls.append(1))
    QueueMember.objects.filter(queue=queue).delete()
    assert calls


def test_a_redis_outage_never_blocks_a_save(settings, django_capture_on_commit_callbacks, caplog):
    settings.BUS_REDIS_URL = "redis://127.0.0.1:1/0"
    bus.client.cache_clear()
    try:
        with django_capture_on_commit_callbacks(execute=True):
            agents.create_agent(actor=None, number="7001", name="A")
    finally:
        bus.client.cache_clear()
    assert "config.changed not sent" in caplog.text


def test_the_manager_role_opens_the_admin_pages(make_user, grant):
    manager = make_user(account_type="Internal", is_staff=True)
    grant(manager, "can_view_queues", "can_edit_queues", "can_view_agents", "can_manage_agents")
    browser = Client()
    browser.force_login(manager)
    for page in ("/admin/queues/queue/add/", "/admin/queues/entrypoint/add/", "/admin/agents/agent/add/"):
        assert browser.get(page).status_code == 200, page

    stranger = make_user(account_type="Internal", is_staff=True)
    browser.force_login(stranger)
    assert browser.get("/admin/queues/queue/").status_code == 403
    assert browser.get("/admin/calls/call/").status_code == 403


def test_settings_point_tests_at_their_own_redis_database():
    assert settings.BUS_REDIS_URL.endswith("/5")
