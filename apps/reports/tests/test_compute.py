"""M9: reports from the Event table, on one scripted day whose every number is worked out by hand.

Queues: Support (target 20 s), Backup (target 30 s). Times are seconds after 08:00.
  1  Support 0, agent A answers 10, A's leg ends 70, A wraps up 85          answered (wait 10, talk 60, wrap 15)
  2  Support 100, hangs up 103                                             short abandon (3 s)
  3  Support 200, hangs up 240                                             abandon (40 s)
  4  Support 300, overflow to Backup 360, C answers 390, ends 450          Support: overflowed; Backup: answered (30)
  5  AI 500-560 (handoff, asked for a person), Support 560, B answers 565,
     ends 625, B wraps up 630                                              answered (wait 5, talk 60, wrap 5)
  6  AI 700-730 resolved                                                   no queue
  7  AI 800-810 failed, Backup 810, hangs up 900                           Backup: abandon (90 s)
  8  Support 1000, voicemail 1100, ends 1130                               voicemail (waited 100)
  9  started yesterday                                                     not in today's report
"""

from datetime import UTC, datetime, timedelta

import pytest

from apps.reports.compute import Row, compute
from contracts import messages as m

T0 = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
SINCE, UNTIL = T0, T0 + timedelta(seconds=1800)
A, B, C, AI = "agent-a", "agent-b", "agent-c", "ai-1"


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def call_rows() -> list[Row]:
    rows: list[tuple[float, str, dict]] = []

    def add(t, kind, **data):
        rows.append((t, kind, data))

    def queued(t, call, queue, reason="entry"):
        add(t, m.CALL_QUEUED, call=call, queue=queue, reason=reason)

    def answered(t, call, agent, leg):
        add(t, m.CALL_CONNECTED, call=call, agent=agent, leg=leg)

    def leg_ended(t, call, leg):
        add(t, m.LEG_ENDED, call=call, leg=leg, role="agent")

    def ended(t, call, result):
        add(t, m.CALL_ENDED, call=call, result=result, reason="")

    for call, t, queue in (
        ("c1", 0, "Support"),
        ("c2", 100, "Support"),
        ("c3", 200, "Support"),
        ("c4", 300, "Support"),
    ):
        add(t, m.CALL_STARTED, call=call, queue=queue, entry="800")
        queued(t, call, queue)
    answered(10, "c1", A, "a1")
    leg_ended(70, "c1", "a1")
    ended(70, "c1", "answered")
    add(85, m.AGENT_WRAPPED_UP, call="c1", agent=A, code="Sale")
    ended(103, "c2", "abandoned")
    ended(240, "c3", "abandoned")
    add(360, m.CALL_OVERFLOWED, call="c4", from_queue="Support", to_queue="Backup", reason="max wait")
    queued(360, "c4", "Backup", "overflow")
    answered(390, "c4", C, "c-leg")
    leg_ended(450, "c4", "c-leg")
    ended(450, "c4", "answered")

    add(500, m.CALL_STARTED, call="c5", queue="General", entry="800")
    add(500, m.AI_STARTED, call="c5", leg="ai5", ai_agent=AI)
    add(560, m.AI_ENDED, call="c5", outcome="handoff", reason="asked_for_person", ai_agent=AI)
    queued(560, "c5", "Support", "ai_handoff")
    answered(565, "c5", B, "b5")
    leg_ended(625, "c5", "b5")
    ended(625, "c5", "answered")
    add(630, m.AGENT_WRAPPED_UP, call="c5", agent=B, code="Information given")

    add(700, m.CALL_STARTED, call="c6", queue="General", entry="802")
    add(700, m.AI_STARTED, call="c6", leg="ai6", ai_agent=AI)
    add(730, m.AI_ENDED, call="c6", outcome="resolved", reason="answered the question", ai_agent=AI)
    ended(730, "c6", "ai_handled")

    add(800, m.CALL_STARTED, call="c7", queue="General", entry="800")
    add(800, m.AI_STARTED, call="c7", leg="ai7", ai_agent=AI)
    add(810, m.AI_ENDED, call="c7", outcome="failed", reason="the AI did not come up", ai_agent=AI)
    queued(810, "c7", "Backup", "ai_failed")
    ended(900, "c7", "abandoned")

    add(1000, m.CALL_STARTED, call="c8", queue="Support", entry="899")
    queued(1000, "c8", "Support")
    add(1100, m.CALL_VOICEMAIL, call="c8", queue="Support", reason="max wait reached")
    ended(1130, "c8", "voicemail")

    add(-86400, m.CALL_STARTED, call="c9", queue="Support", entry="800")
    queued(-86400, "c9", "Support")
    ended(-86390, "c9", "abandoned")
    return [Row(kind, at(t), data) for t, kind, data in sorted(rows, key=lambda row: row[0])]


def state(t, agent, availability, activity="idle", reason=""):
    return Row(
        m.AGENT_STATE,
        at(t),
        {"agent": agent, "availability": availability, "activity": activity, "reason": reason},
    )


def state_rows() -> list[Row]:
    return [
        state(-3600, A, "ready"),  # before the report: A was Ready when it began
        state(5, A, "ready", "offered"),
        state(10, A, "ready", "talking"),
        state(70, A, "ready", "wrapup"),
        state(85, A, "ready"),
        state(200, A, "not_ready", reason="Lunch"),
        state(1200, A, "logged_out"),
    ]


@pytest.fixture
def report():
    return compute(
        call_rows(),
        state_rows(),
        since=SINCE,
        until=UNTIL,
        targets={"Support": (20, 80), "Backup": (30, 80)},
    )


def line(report, queue):
    return next(q for q in report.queues if q.queue == queue)


def test_calls_per_queue(report):
    support, backup = line(report, "Support"), line(report, "Backup")
    assert (support.offered, support.answered, support.abandoned, support.short_abandoned) == (6, 2, 2, 1)
    assert (support.overflowed, support.voicemail) == (1, 1)
    assert (backup.offered, backup.answered, backup.abandoned, backup.overflowed) == (2, 1, 1, 0)
    assert [q.queue for q in report.queues] == ["Backup", "Support"], "by name; General never held a caller"
    total = report.total
    assert (total.queue, total.offered, total.answered, total.abandoned) == ("All queues", 8, 3, 3)


def test_service_level_and_abandon_rate(report):
    support, backup = line(report, "Support"), line(report, "Backup")
    assert (support.answered_within, support.service_level_percent, support.met_target) == (2, 33.3, False)
    assert support.abandon_rate_percent == 33.3
    assert (backup.answered_within, backup.service_level_percent) == (1, 50.0), "30 s within a 30 s target"
    assert backup.abandon_rate_percent == 50.0
    assert report.total.service_level_percent is None, "each queue has its own target: no total service level"


def test_waits_and_handle_time(report):
    support, backup = line(report, "Support"), line(report, "Backup")
    assert support.average_speed_of_answer == 7.5
    assert support.longest_wait == 100.0, "the voicemail caller waited longest"
    assert (support.average_talk, support.average_wrapup, support.average_handle_time) == (60.0, 10.0, 70.0)
    assert (backup.average_speed_of_answer, backup.longest_wait, backup.average_handle_time) == (
        30.0,
        90.0,
        60.0,
    )


def test_agent_time_in_state(report):
    agent_a = next(a for a in report.agents if a.agent == A)
    assert agent_a.seconds == {
        "available": 120.0,
        "ringing": 5.0,
        "talking": 60.0,
        "wrapup": 15.0,
        "not_ready: Lunch": 1000.0,
    }, "logged out time is not counted"
    assert (agent_a.answered, agent_a.average_handle_time) == (1, 75.0)
    agent_b = next(a for a in report.agents if a.agent == B)
    assert (agent_b.answered, agent_b.average_handle_time, agent_b.seconds) == (1, 65.0, {})


def test_ai_containment_and_handoff_reasons(report):
    [ai] = report.ai
    assert (ai.ai_agent, ai.calls, ai.resolved, ai.handed_off, ai.failed, ai.caller_left) == (
        AI,
        3,
        1,
        1,
        1,
        0,
    )
    assert ai.containment_percent == 33.3
    assert ai.reasons == {"asked_for_person": 1}
    assert ai.average_seconds == 33.3


def test_an_empty_period():
    report = compute([], [], since=SINCE, until=UNTIL, targets={})
    assert report.queues == [] and report.agents == [] and report.ai == []
    assert report.total.offered == 0 and report.total.service_level_percent is None
    assert report.total.abandon_rate_percent is None and report.total.average_handle_time is None


def test_calls_from_before_m9_have_no_call_queued():
    """Older events: the call's queue comes from call.started (no AI, no call.queued)."""
    rows = [
        Row(m.CALL_STARTED, at(0), {"call": "old", "queue": "Support"}),
        Row(m.CALL_CONNECTED, at(12), {"call": "old", "agent": A, "leg": "x"}),
        Row(m.CALL_ENDED, at(40), {"call": "old", "result": "answered"}),
    ]
    report = compute(rows, [], since=SINCE, until=UNTIL, targets={"Support": (20, 80)})
    support = line(report, "Support")
    assert (support.offered, support.answered, support.answered_within, support.average_talk) == (
        1,
        1,
        1,
        28.0,
    )


def test_a_brain_restart_queues_the_same_call_twice_but_it_is_offered_once():
    rows = [
        Row(m.CALL_STARTED, at(0), {"call": "r", "queue": "Support"}),
        Row(m.CALL_QUEUED, at(0), {"call": "r", "queue": "Support", "reason": "entry"}),
        Row(m.CALL_QUEUED, at(30), {"call": "r", "queue": "Support", "reason": "adopted"}),
        Row(m.CALL_ENDED, at(50), {"call": "r", "result": "abandoned"}),
    ]
    support = line(compute(rows, [], since=SINCE, until=UNTIL, targets={}), "Support")
    assert (support.offered, support.abandoned, support.longest_wait) == (1, 1, 50.0)
