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
    assert config.entries == {"800": str(support.uid)}, "an entry point to an inactive queue leads nowhere"
