"""manage.py seed_demo writes the demo centre of ROUTING_DESIGN.md section 3; the brain then sees
exactly the configuration its routing tests use (brain/sim/demo.py)."""

import pytest
from django.core.management import call_command

from apps.agents.models import Agent, Skill
from apps.queues.models import EntryPoint, Queue, SkillRuleSet
from apps.teams.models import Team
from brain.adapters.config import load_config
from brain.sim import demo

PASSWORD = "Demo-password-2026"


def by_name(config):
    """The configuration with database uids swapped for names, to compare with demo.core_config()."""
    agent = {uid: f"agent-{a.name.lower()}" for uid, a in config.agents.items()}
    queue = {uid: q.name.lower() for uid, q in config.queues.items()}
    agents = {agent[uid]: (a.number, a.name, a.skills) for uid, a in config.agents.items()}
    queues = {
        queue[uid]: (
            q.strategy,
            q.priority,
            q.ring_timeout,
            q.max_wait,
            q.overflow_action,
            queue.get(q.overflow_queue),
            q.steps,
            tuple(agent[member] for member in q.members),
        )
        for uid, q in config.queues.items()
    }
    entries = {
        n: (queue[e.queue], e.language, e.priority, queue.get(e.fallback_queue))
        for n, e in config.entries.items()
    }
    return agents, queues, entries, config.skills


@pytest.mark.django_db(transaction=True)
def test_seed_demo_gives_the_brain_the_demo_centre_and_is_safe_to_run_twice():
    call_command("seed_demo", password=PASSWORD)
    call_command("seed_demo", password=PASSWORD)

    assert Skill.objects.count() == 7 and Agent.objects.count() == 8
    assert (
        SkillRuleSet.objects.count() == 5 and Queue.objects.count() == 5 and EntryPoint.objects.count() == 3
    )
    teams = {team.name: team for team in Team.objects.all()}
    assert [user.first_name for user in teams["Front Desk"].supervisors.all()] == ["Asha"]
    assert sorted(user.first_name for user in teams["Claims & Providers"].agents.all()) == [
        "Halima",
        "Mussa",
        "Peter",
        "Zawadi",
    ]

    seeded = by_name(load_config())
    expected = demo.core_config()
    expected_by_name = (
        {uid: (a.number, a.name, a.skills) for uid, a in expected.agents.items()},
        {
            uid: (
                q.strategy,
                q.priority,
                q.ring_timeout,
                q.max_wait,
                q.overflow_action,
                q.overflow_queue,
                q.steps,
                q.members,
            )
            for uid, q in expected.queues.items()
        },
        {n: (e.queue, e.language, e.priority, e.fallback_queue) for n, e in expected.entries.items()},
        expected.skills,
    )
    assert seeded == expected_by_name

    # M8: Jambo AI answers 800 and 802 first, and may send callers to the five queues.
    from apps.ai.models import AiAgent

    [jambo] = AiAgent.objects.all()
    config = load_config()
    assert {n for n, e in config.entries.items() if e.ai_agent == str(jambo.uid)} == {"800", "802"}
    assert [route.queue.name for route in jambo.routes.all()] == [
        "Membership",
        "Claims",
        "Providers",
        "Escalations",
        "General",
    ]
    assert jambo.routes.get(queue__name="Claims").skill.name == "claims"


@pytest.mark.django_db(transaction=True)
def test_demo_agents_sign_in_with_the_demo_password(sign_in):
    call_command("seed_demo", password=PASSWORD)
    status, body = sign_in("juma@example.org", PASSWORD)
    assert status == 200 and "can_take_calls" in body["user"]["authorities"]
