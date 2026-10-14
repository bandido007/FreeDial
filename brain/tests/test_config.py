"""The brain's configuration comes from the database, as plain data, only when loaded."""

import pytest

from apps.agents import services as agents
from apps.queues import services as queues
from brain.adapters.config import load_config


@pytest.mark.django_db(transaction=True)
def test_load_config_reads_active_agents_queues_and_entry_points():
    a = agents.create_agent(actor=None, number="7001", name="Asha")
    b = agents.create_agent(actor=None, number="7002", name="Baraka")
    gone = agents.create_agent(actor=None, number="7003", name="Gone")
    support = queues.save_queue(actor=None, name="Support", ring_timeout=12, max_wait=90, agents=[b, a, gone])
    quiet = queues.save_queue(actor=None, name="Quiet")
    queues.save_entry_point(actor=None, number="800", queue=support)
    queues.save_entry_point(actor=None, number="801", queue=quiet)
    agents.delete_agent(uid=gone.uid)
    quiet.deactivate()

    config = load_config()

    assert set(config.agents) == {str(a.uid), str(b.uid)}
    queue = config.queues[str(support.uid)]
    assert (queue.name, queue.ring_timeout, queue.max_wait) == ("Support", 12, 90)
    assert queue.members == (str(a.uid), str(b.uid)), (
        "members in priority, then number order; deleted agents left out"
    )
    assert list(config.entries) == ["800"], "an entry point to an inactive queue leads nowhere"
    assert config.entries["800"].queue == str(support.uid)


@pytest.mark.django_db(transaction=True)
def test_load_config_carries_skills_strategy_limits_overflow_and_rules():
    from apps.agents.models import AgentSkill, Skill
    from brain.core.config import EntryConfig, Requirement, Step

    agents.create_skill(actor=None, name="swahili", kind="language")
    agents.create_skill(actor=None, name="billing", kind="topic")
    asha = agents.create_agent(actor=None, number="7001", name="Asha")
    agents.set_agent_skills(agent=asha, levels={"swahili": 9, "billing": 3})
    rules = queues.save_skill_rule_set(
        actor=None, name="Swahili", steps=[(30, {"$language": 6, "billing": 3}), (None, {"$language": 4})]
    )
    backup = queues.save_queue(actor=None, name="Backup")
    support = queues.save_queue(
        actor=None, name="Support", agents=[asha], strategy="round_robin", max_callers=5, priority=3
    )
    support.overflow_action, support.overflow_queue, support.skill_rule_set = "queue", backup, rules
    support.save()
    queues.save_entry_point(actor=None, number="800", queue=support)
    entry = support.entry_points.get()
    entry.language, entry.priority, entry.fallback_queue = Skill.objects.get(name="swahili"), 7, backup
    entry.save()
    assert AgentSkill.objects.count() == 2

    config = load_config()

    assert config.agents[str(asha.uid)].skills == {"swahili": 9, "billing": 3}
    queue = config.queues[str(support.uid)]
    assert (queue.strategy, queue.max_callers, queue.priority) == ("round_robin", 5, 3)
    assert (queue.overflow_action, queue.overflow_queue) == ("queue", str(backup.uid))
    assert queue.steps == (
        Step(30, (Requirement("$language", 6), Requirement("billing", 3))),
        Step(None, (Requirement("$language", 4),)),
    ), "in time order, forever last"
    assert config.entries["800"] == EntryConfig("800", str(support.uid), "swahili", 7, str(backup.uid))
    assert config.skills == {"swahili": "language", "billing": "topic"}


def test_the_admins_strategy_choices_are_the_brains_strategies():
    from apps.queues.models import StrategyChoices
    from brain.core.config import Strategy

    assert set(StrategyChoices.values) == {strategy.value for strategy in Strategy}
