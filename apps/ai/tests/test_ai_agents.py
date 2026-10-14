"""AI agents in the admin: who may see and change them, and the brain hears of every change."""

import pytest

from apps.ai.models import AiAgent, AiRoute
from apps.ai.permissions import EDIT_AI_AGENTS, VIEW_AI_AGENTS
from apps.queues import services as queues

pytestmark = pytest.mark.django_db


def test_a_change_tells_the_brain(monkeypatch):
    from apps.ai import receivers

    calls = []
    monkeypatch.setattr(receivers, "config_changed", lambda: calls.append(1))
    ai = AiAgent.objects.create(name="Jambo AI", instructions="A helpline.")
    AiRoute.objects.create(ai_agent=ai, queue=queues.save_queue(actor=None, name="General"), when="anything")
    ai.delete()
    assert len(calls) == 3


def test_the_admin_pages_follow_the_permissions(client, make_user, grant):
    ai = AiAgent.objects.create(
        name="Jambo AI", instructions="A helpline.", sensitive_topics="overdose\n\n sumu "
    )
    page, change = "/admin/ai/aiagent/", f"/admin/ai/aiagent/{ai.pk}/change/"
    viewer = make_user(account_type="Internal", is_staff=True)
    grant(viewer, VIEW_AI_AGENTS)
    client.force_login(viewer)
    assert client.get(page).status_code == 200 and b"Jambo AI" in client.get(page).content
    assert client.post(change, {"name": "Changed"}).status_code == 403
    editor = make_user(account_type="Internal", is_staff=True)
    grant(editor, VIEW_AI_AGENTS, EDIT_AI_AGENTS)
    client.force_login(editor)
    response = client.get(change)
    assert response.status_code == 200 and b"Where it may send callers" in response.content
    stranger = make_user(account_type="Internal", is_staff=True)
    client.force_login(stranger)
    assert client.get(page).status_code == 403
    assert ai.topics() == ["overdose", "sumu"]


def test_a_new_ai_agent_gets_the_bake_off_winners():
    ai = AiAgent.objects.create(name="New", instructions="-")
    assert (ai.stt_model, ai.llm_model, ai.tts_model, ai.voice) == (
        "gpt-4o-transcribe",
        "gpt-4o",
        "gpt-4o-mini-tts",
        "coral",
    )
    assert (ai.max_failed_turns, ai.start_timeout) == (2, 10)
