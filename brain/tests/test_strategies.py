"""M4: each strategy's choice on scripted agent states, skill rules that widen on the second,
max callers, overflow and queue priority. The core alone, with exact times."""

import itertools
from dataclasses import replace

import pytest

from brain.core import actions as a
from brain.core.agents import Activity
from brain.core.brain import Brain
from brain.core.config import AgentConfig, Config, QueueConfig, SkillRule, Strategy
from contracts import messages as m

NUMBERS = ("7001", "7002", "7003")


def uid(number):
    return f"agent-{number}"


class Desk:
    """A core with three agents in queue Support, logged in on SIM/<number>, made Ready at given times."""

    def __init__(self, strategy=Strategy.LONGEST_IDLE, ready_at=(0, 0, 0), skills=None, **queue):
        skills = skills or {}
        support = QueueConfig(
            uid="support", name="Support", strategy=strategy, members=tuple(map(uid, NUMBERS))
        )
        self.config = Config(
            agents={uid(n): AgentConfig(uid(n), n, f"Agent {n}", skills.get(n, {})) for n in NUMBERS},
            queues={"support": replace(support, **queue)},
            entries={"800": "support"},
        )
        ids = itertools.count(1)
        self.core = Brain(self.config, app="algodial", new_id=lambda: f"id{next(ids)}")
        self.calls = 0
        for number, when in zip(NUMBERS, ready_at, strict=True):
            self.core.on_command(
                m.AGENT_LOGIN, {"agent": uid(number), "endpoint": f"SIM/{number}"}, "c", when
            )
            self.core.on_command(m.AGENT_READY, {"agent": uid(number)}, "c", when)

    def call(self, now, entry="800"):
        """A customer arrives; returns the agent numbers rung, in order, and all actions."""
        self.calls += 1
        cid = f"c{self.calls}"
        event = {
            "type": "StasisStart",
            "args": ["inbound", entry, cid, cid],
            "channel": {"id": cid, "caller": {"name": "C", "number": cid}},
        }
        actions = self.core.on_ari(event, now)
        return cid, rung(actions), actions

    def answer(self, cid, number, now):
        leg = next(leg for leg, agent in self.core.calls[cid].offers.items() if agent == uid(number))
        return self.core.on_ari(
            {"type": "StasisStart", "args": ["agent", cid, uid(number)], "channel": {"id": leg}}, now
        )

    def finish(self, cid, now):
        """The customer hangs up; returns the actions (the next offers among them)."""
        return self.core.on_ari({"type": "StasisEnd", "channel": {"id": cid}}, now)

    def ring_out(self, cid, number, now, cause=19):
        leg = next(leg for leg, agent in self.core.calls[cid].offers.items() if agent == uid(number))
        return self.core.on_ari({"type": "ChannelDestroyed", "cause": cause, "channel": {"id": leg}}, now)

    def agent(self, number):
        return self.core.agents[uid(number)]


def rung(actions):
    return [action.endpoint.split("/")[1] for action in actions if isinstance(action, a.Originate)]


def timers(actions, prefix):
    return [action for action in actions if isinstance(action, a.SetTimer) and action.key.startswith(prefix)]


# ── each strategy's choice ────────────────────────────────────────────────
def test_longest_idle_rings_who_has_waited_longest_since_ready():
    desk = Desk(Strategy.LONGEST_IDLE, ready_at=(10, 5, 20))
    assert desk.call(now=30)[1] == ["7002"]


def test_linear_always_starts_from_the_first_member():
    desk = Desk(Strategy.LINEAR, ready_at=(30, 5, 20))
    first, rung_first, _ = desk.call(now=40)
    assert rung_first == ["7001"], "member order, not idle time"
    desk.answer(first, "7001", now=41)
    assert desk.call(now=42)[1] == ["7002"], "7001 is busy: the next member"


def test_round_robin_carries_on_after_the_last_agent_rung():
    desk = Desk(Strategy.ROUND_ROBIN, ready_at=(30, 5, 20))
    order = []
    for second in (40, 50, 60, 70):
        cid, rung_now, _ = desk.call(now=second)
        order += rung_now
        desk.answer(cid, rung_now[0], now=second + 1)
        desk.finish(cid, now=second + 2)
    assert order == ["7001", "7002", "7003", "7001"]


def test_round_robin_skips_a_busy_member_and_remembers_where_it_was():
    desk = Desk(Strategy.ROUND_ROBIN)
    c1, rung1, _ = desk.call(now=10)  # 7001
    desk.answer(c1, "7001", now=11)
    c2, rung2, _ = desk.call(now=12)  # 7002
    desk.answer(c2, "7002", now=13)
    desk.finish(c1, now=14)  # 7001 free again, 7002 still talking
    assert (rung1, rung2, desk.call(now=15)[1]) == (["7001"], ["7002"], ["7003"])


def test_fewest_calls_rings_who_answered_the_fewest_calls_of_this_queue():
    desk = Desk(Strategy.FEWEST_CALLS, ready_at=(0, 0, 0))
    for number in ("7001", "7001", "7003"):  # 7001 answered 2, 7003 answered 1, 7002 none
        desk.core.answered.setdefault("support", {})[uid(number)] = (
            desk.core.answered.get("support", {}).get(uid(number), 0) + 1
        )
    assert desk.call(now=10)[1] == ["7002"]


def test_fewest_calls_counts_answered_calls_as_they_happen():
    desk = Desk(Strategy.FEWEST_CALLS, ready_at=(0, 1, 2))
    seen = []
    for second in (10, 20, 30, 40, 50, 60):
        cid, rung_now, _ = desk.call(now=second)
        seen += rung_now
        desk.answer(cid, rung_now[0], now=second + 1)
        desk.finish(cid, now=second + 2)
    assert sorted(seen) == ["7001", "7001", "7002", "7002", "7003", "7003"]


def test_random_draws_from_the_ids_so_a_replay_repeats_it():
    picks = []
    for _ in range(2):
        desk = Desk(Strategy.RANDOM)
        picks.append([desk.call(now=10 + n)[1] for n in range(3)])
    assert picks[0] == picks[1] and all(len(choice) == 1 for choice in picks[0])


def test_random_spreads_over_every_free_agent():
    seen = set()
    for start in range(40):
        desk = Desk(Strategy.RANDOM)
        for _ in range(start):
            desk.core.new_id()  # different ids, different draws
        seen.update(desk.call(now=10)[1])
    assert seen == set(NUMBERS)


def test_ring_all_rings_every_free_agent_and_the_first_answer_takes_it():
    desk = Desk(Strategy.RING_ALL)
    cid, rung_now, _ = desk.call(now=10)
    assert sorted(rung_now) == list(NUMBERS)
    actions = desk.answer(cid, "7002", now=12)
    stopped = [action.channel for action in actions if isinstance(action, a.Hangup)]
    assert len(stopped) == 2, "the other two phones stop ringing"
    assert desk.agent("7002").activity is Activity.TALKING
    assert desk.agent("7001").activity is Activity.IDLE and desk.agent("7003").activity is Activity.IDLE
    reasons = [x.data["reason"] for x in actions if isinstance(x, a.Emit) and x.type == m.CALL_OFFER_FAILED]
    assert reasons == ["answered elsewhere", "answered elsewhere"]


def test_ring_all_ringing_out_is_nobodys_fault():
    desk = Desk(Strategy.RING_ALL)
    cid, _, _ = desk.call(now=10)
    for number in NUMBERS:
        desk.ring_out(cid, number, now=30)
    assert {desk.agent(n).activity for n in NUMBERS} == {Activity.IDLE}, "nobody becomes Not responding"


def test_a_lone_agent_who_rings_out_still_becomes_not_responding():
    desk = Desk(Strategy.LINEAR)
    cid, _, _ = desk.call(now=10)
    desk.ring_out(cid, "7001", now=30)
    assert desk.agent("7001").activity is Activity.NOT_RESPONDING


# ── skills that widen with waiting time ───────────────────────────────────
RULES = (
    SkillRule(0, "swahili >= 8"),
    SkillRule(30, "swahili >= 5"),
    SkillRule(60, "true"),
)
SKILLS = {"7001": {"swahili": 6}, "7002": {"english": 9}, "7003": {"swahili": 9}}


def test_the_strict_rule_picks_the_expert():
    desk = Desk(skill_rules=RULES, skills=SKILLS)
    assert desk.call(now=0)[1] == ["7003"]


def test_a_skill_rule_widens_at_the_right_second():
    desk = Desk(skill_rules=RULES, skills=SKILLS)
    busy, _, _ = desk.call(now=0)  # the expert 7003 takes the first call
    desk.answer(busy, "7003", now=1)

    cid, rung_now, actions = desk.call(now=100)
    assert rung_now == [], "nobody has swahili >= 8 free"
    [widen] = timers(actions, "widen:")
    assert widen.seconds == 30, "the brain wakes up exactly when the next rule starts"

    assert rung(desk.core.on_timer(widen.key, now=130)) == ["7001"], "at 30 s swahili >= 5 lets 7001 in"


def test_the_widest_rule_lets_anyone_in_at_its_second():
    desk = Desk(skill_rules=RULES, skills={"7001": {}, "7002": {}, "7003": {}})
    cid, rung_now, actions = desk.call(now=0)
    assert rung_now == []
    first = timers(actions, "widen:")[0]
    assert first.seconds == 30
    second_actions = desk.core.on_timer(first.key, now=30)
    assert rung(second_actions) == [] and timers(second_actions, "widen:")[0].seconds == 30
    assert rung(desk.core.on_timer(first.key, now=60)) == ["7001"]


def test_an_agent_who_becomes_ready_is_checked_against_the_rule_in_force_then():
    desk = Desk(skill_rules=RULES, skills=SKILLS, ready_at=(0, 0, 999))
    desk.core.on_command(m.AGENT_NOT_READY, {"agent": uid("7003"), "reason": "x"}, "c", 1)
    cid, rung_now, _ = desk.call(now=10)
    assert rung_now == []
    ready = desk.core.on_command(m.AGENT_READY, {"agent": uid("7003")}, "c", 20)
    assert rung(ready) == ["7003"], "the expert comes back before the rule widens: offered at once"


# ── limits, overflow, priority ────────────────────────────────────────────
def two_queues(**support):
    desk = Desk(**support)
    backup = QueueConfig(uid="backup", name="Backup", members=(uid("7003"),), max_wait=120)
    desk.config = replace(desk.config, queues={**desk.config.queues, "backup": backup})
    desk.core.load_config(desk.config, now=0)
    return desk


def test_max_callers_sends_the_extra_caller_to_the_overflow_queue():
    desk = two_queues(max_callers=1, overflow_queue="backup", members=(uid("7001"),))
    desk.core.on_command(m.AGENT_NOT_READY, {"agent": uid("7001"), "reason": "x"}, "c", 1)
    desk.call(now=10)
    _, _, actions = desk.call(now=11)
    [moved] = [x.data for x in actions if isinstance(x, a.Emit) and x.type == m.CALL_OVERFLOWED]
    assert (moved["from_queue"], moved["to_queue"], moved["reason"]) == ("Support", "Backup", "queue full")
    assert rung(actions) == ["7003"]


def test_max_wait_overflows_and_the_new_queue_counts_its_own_wait():
    desk = two_queues(overflow_queue="backup", max_wait=60, members=(uid("7001"),))
    desk.core.on_command(m.AGENT_NOT_READY, {"agent": uid("7001"), "reason": "x"}, "c", 1)
    cid, _, _ = desk.call(now=0)
    actions = desk.core.on_timer(f"maxwait:{cid}", now=60)
    assert rung(actions) == ["7003"]
    assert [x.seconds for x in timers(actions, "maxwait:")] == [120]
    assert desk.core.calls[cid].queue == "backup"


def test_overflow_never_loops():
    desk = two_queues(overflow_queue="backup", members=(uid("7001"),))
    backup = replace(desk.config.queues["backup"], overflow_queue="support", members=())
    desk.core.load_config(replace(desk.config, queues={**desk.config.queues, "backup": backup}), now=0)
    desk.core.on_command(m.AGENT_NOT_READY, {"agent": uid("7001"), "reason": "x"}, "c", 1)
    cid, _, _ = desk.call(now=0)
    desk.core.on_timer(f"maxwait:{cid}", now=300)  # to Backup
    ended = desk.core.on_timer(f"maxwait:{cid}", now=420)  # Backup would send it back: hang up instead
    assert [x.data["result"] for x in ended if isinstance(x, a.Emit) and x.type == m.CALL_ENDED] == [
        "overflow"
    ]


def test_a_free_agent_takes_the_higher_priority_queue_first():
    desk = two_queues(members=(uid("7003"),))
    backup = replace(desk.config.queues["backup"], priority=5)
    desk.core.load_config(
        replace(
            desk.config,
            entries={"800": "support", "900": "backup"},
            queues={**desk.config.queues, "backup": backup},
        ),
        now=0,
    )
    desk.core.on_command(m.AGENT_NOT_READY, {"agent": uid("7003"), "reason": "x"}, "c", 1)
    early, _, _ = desk.call(now=10)  # Support, waiting longest
    late, _, _ = desk.call(now=20, entry="900")  # Backup, priority 5
    ready = desk.core.on_command(m.AGENT_READY, {"agent": uid("7003")}, "c", 30)
    offered = [x.data["call"] for x in ready if isinstance(x, a.Emit) and x.type == m.CALL_OFFERED]
    assert offered == [late]


@pytest.mark.parametrize("strategy", list(Strategy))
def test_every_strategy_offers_only_to_free_matching_untried_agents(strategy):
    desk = Desk(
        strategy,
        skill_rules=(SkillRule(0, "swahili"),),
        skills={"7001": {"swahili": 1}, "7002": {"swahili": 3}},
    )
    desk.core.on_command(m.AGENT_NOT_READY, {"agent": uid("7002"), "reason": "x"}, "c", 1)
    assert desk.call(now=10)[1] == ["7001"], "7002 is Not Ready and 7003 lacks swahili"
