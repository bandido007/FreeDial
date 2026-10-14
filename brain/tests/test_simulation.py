"""M4 by simulation: the real core under scripted traffic on a virtual clock (brain/sim/simulator.py)."""

import random

import pytest

from brain.core.config import QueueConfig, Requirement, Step, Strategy
from brain.sim.simulator import Simulation, VirtualAgent, VirtualCall


def support(**settings):
    return QueueConfig(uid="support", name="Support", **{"wrapup_seconds": 0, **settings})


def one_at_a_time(count, gap=100, talk=30):
    """Calls that never overlap: every agent is free when each one arrives."""
    return [VirtualCall(at=n * gap, talk=talk) for n in range(count)]


AGENTS = [VirtualAgent("7001"), VirtualAgent("7002"), VirtualAgent("7003")]


def run(strategy, calls, agents=AGENTS, **settings):
    return Simulation(queues=[support(strategy=strategy, **settings)], agents=agents, calls=calls).run()


def test_linear_keeps_giving_calls_to_the_first_member():
    assert run(Strategy.LINEAR, one_at_a_time(30)).answered_by() == {"7001": 30, "7002": 0, "7003": 0}


@pytest.mark.parametrize("strategy", [Strategy.ROUND_ROBIN, Strategy.LONGEST_IDLE, Strategy.FEWEST_CALLS])
def test_the_fair_strategies_share_calls_out_evenly(strategy):
    assert run(strategy, one_at_a_time(30)).answered_by() == {"7001": 10, "7002": 10, "7003": 10}


def test_random_spreads_calls_over_everyone():
    shares = run(Strategy.RANDOM, one_at_a_time(300)).answered_by()
    assert all(70 <= count <= 130 for count in shares.values()), shares


def test_ring_all_rings_everyone_and_the_quickest_answers():
    agents = [
        VirtualAgent("7001", answer_after=4),
        VirtualAgent("7002", answer_after=1.5),
        VirtualAgent("7003", answer_after=3),
    ]
    report = run(Strategy.RING_ALL, one_at_a_time(10), agents=agents)
    assert all(sorted(call.rung) == ["7001", "7002", "7003"] for call in report.calls)
    assert report.answered_by() == {"7001": 0, "7002": 10, "7003": 0}
    assert all(call.waited == 1.5 for call in report.calls)


def test_round_robin_moves_past_an_agent_who_rejects():
    agents = [VirtualAgent("7001"), VirtualAgent("7002", behaviour="reject"), VirtualAgent("7003")]
    report = run(Strategy.ROUND_ROBIN, one_at_a_time(6), agents=agents)
    assert report.answered_by() == {"7001": 3, "7002": 0, "7003": 3}
    assert report.agents["7002"].refused == 3


def test_a_skill_rule_widens_at_the_right_second_in_simulated_time():
    agents = [VirtualAgent("7001", skills={"swahili": 6}), VirtualAgent("7002", skills={"english": 9})]
    rules = (Step(30, (Requirement("swahili", 8),)), Step(60, (Requirement("swahili", 5),)), Step(None, ()))
    report = run(
        Strategy.LONGEST_IDLE,
        [VirtualCall(at=7, talk=200), VirtualCall(at=8, talk=200)],
        agents=agents,
        steps=rules,
    )
    first, second = report.calls
    assert (first.agent, first.answered_at) == ("7001", 7 + 30 + 2.0), (
        "offered at second 30 of waiting, answered 2 s later"
    )
    assert (second.agent, second.answered_at) == ("7002", 8 + 60 + 2.0), (
        "only the anyone-rule at 60 s lets 7002 in"
    )


def test_max_wait_overflows_to_another_queue_whose_agents_answer():
    backup = QueueConfig(uid="backup", name="Backup", wrapup_seconds=0)
    agents = [VirtualAgent("7001", behaviour="offline"), VirtualAgent("7009", queues=("backup",))]
    sim = Simulation(
        queues=[support(max_wait=45, overflow_queue="backup"), backup],
        agents=agents,
        calls=[VirtualCall(at=0)],
    )
    [call] = sim.run().calls
    assert (call.queue, call.agent, call.answered_at) == ("Backup", "7009", 47.0)


def test_a_busy_hour_ends_cleanly_with_honest_numbers():
    """Many callers, few agents, rejections, a slow agent, impatient customers and wrap-up."""
    rng = random.Random(4)
    calls, at = [], 0.0
    for _ in range(240):
        at += rng.expovariate(1 / 15)  # a caller every 15 s on average
        calls.append(VirtualCall(at=at, talk=rng.uniform(30, 240), patience=rng.uniform(20, 300)))
    agents = [
        VirtualAgent("7001"),
        VirtualAgent("7002", answer_after=6),
        VirtualAgent("7003", behaviour="reject"),
        VirtualAgent("7004", wrapup_for=20),
        VirtualAgent("7005", ready_at=600),
    ]
    for strategy in Strategy:
        report = run(strategy, calls, agents=agents, wrapup_seconds=60, ring_timeout=15)
        results = report.results()
        assert sum(results.values()) == 240 and "open" not in results, (strategy, results)
        assert set(results) <= {"answered", "abandoned", "overflow"}
        assert report.answered_by()["7003"] == 0, "the agent who always rejects never takes a call"
        answered = sum(record.answered for record in report.agents.values())
        assert answered == results.get("answered", 0)
        assert 0 <= report.service_level(20) <= 100
