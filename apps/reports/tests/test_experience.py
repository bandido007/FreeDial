"""UX_PRINCIPLES.md section 6: the HEART metrics, computed from the Event table's signals.

Pure: rows in, one line per metric out, each saying what it is based on (so a small sample shows)."""

from datetime import UTC, datetime, timedelta

from apps.reports.compute import Row
from apps.reports.experience import measure

T0 = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def find(lines, metric):
    return next(line for line in lines if line.metric == metric)


def test_offers_answered_quickly_and_missed():
    rows = [Row("offer.answered", at(i), {"agent": "a", "seconds": s}) for i, s in enumerate([4, 6, 20])]
    rows.append(Row("offer.missed", at(9), {"agent": "a", "reason": "no_answer", "seconds": 15}))
    lines = measure(rows, until=at(100))
    assert find(lines, "Median seconds to answer").value == "6.0 s"
    missed = find(lines, "Missed offers per 100")
    assert (missed.value, missed.based_on) == ("25", "1 of 4 offers")


def test_wrapup_time_and_corrections():
    rows = [Row("wrapup.done", at(i), {"agent": "a", "seconds": s}) for i, s in enumerate([10, 30])]
    rows.append(Row("wrapup.code_changed", at(5), {"agent": "a", "code": "Sale", "previous": "Complaint"}))
    lines = measure(rows, until=at(100))
    assert find(lines, "Median wrap-up").value == "20.0 s"
    assert find(lines, "Corrections per 100 calls").value == "50"


def test_transfers_work_first_time():
    rows = [Row("transfer.started", at(i), {"transfer": f"t{i}"}) for i in range(4)]
    rows += [
        Row("transfer.completed", at(10), {"transfer": "t0"}),
        Row("transfer.completed", at(11), {"transfer": "t1"}),
    ]
    rows += [
        Row("transfer.cancelled", at(12), {"transfer": "t2"}),
        Row("transfer.failed", at(13), {"transfer": "t3"}),
    ]
    line = find(measure(rows, until=at(100)), "Transfer success rate")
    assert line.value == "50%" and line.based_on == "2 of 4 transfers; 1 cancelled, 1 failed"


def test_ai_summaries_flagged_wrong_per_100_handoffs():
    rows = [Row("ai.ended", at(i), {"outcome": "handoff"}) for i in range(4)]
    rows += [
        Row("ai.ended", at(5), {"outcome": "resolved"}),
        Row("ux.ai_summary_wrong", at(6), {"agent": "a"}),
    ]
    line = find(measure(rows, until=at(100)), "Summary-wrong rate per 100 AI handoffs")
    assert (line.value, line.based_on) == ("25", "1 of 4 handoffs")


def state(seconds, agent, availability, reason=""):
    return Row("agent.state", at(seconds), {"agent": agent, "availability": availability, "reason": reason})


def test_minutes_from_over_the_limit_to_a_supervisors_action():
    """Break has a 15-minute limit: one agent is forced back 3 minutes after going over, the other comes
    back on their own 1 minute after going over, a third never goes over."""
    rows = [
        state(0, "a", "not_ready", "Break"),
        Row("agent.forced", at(18 * 60), {"agent": "a", "by": "s", "state": "ready"}),
        state(18 * 60, "a", "ready"),
        state(0, "b", "not_ready", "Break"),
        state(16 * 60, "b", "ready"),
        state(0, "c", "not_ready", "Break"),
        state(10 * 60, "c", "ready"),
    ]
    line = find(
        measure(rows, until=at(3600), not_ready_limits={"Break": 15 * 60}), "Median minutes to action"
    )
    assert line.value == "3.0"
    assert line.based_on == "2 times over a limit: 1 acted on by a supervisor, 1 ended by the agent"


def test_admin_setup_time_and_errors_caught():
    created = {"new": at(0), "old": at(-86400 * 40)}
    rows = [
        Row("offer.answered", at(30 * 60), {"agent": "new", "seconds": 3}),
        Row("ux.admin_validation_error", at(1), {"model": "agents.agent", "fields": ["number"]}),
        Row("ux.admin_validation_error", at(2), {"model": "agents.agent", "fields": ["user"]}),
    ]
    lines = measure(rows, until=at(3600), agents_created=created, since=at(-60))
    first = find(lines, "Minutes from creating an agent to their first call")
    assert (first.value, first.based_on) == ("30.0", "1 of 1 new agents took a call")
    errors = find(lines, "Errors caught before saving")
    assert errors.value == "2" and "agents.agent 2" in errors.based_on


def test_speed_budgets_per_button():
    rows = [
        Row("ux.action", at(i), {"action": "holdMyCall", "ms": ms, "ok": True})
        for i, ms in enumerate(range(100, 2100, 100))
    ]
    rows.append(Row("ux.action", at(30), {"action": "holdMyCall", "ms": 10500, "ok": False}))
    line = find(measure(rows, until=at(100)), "95th percentile: holdMyCall")
    assert line.value == "2.0 s (budget 1 s): over"
    assert line.based_on == "21 presses; 1 not confirmed within 10 s"


def test_nothing_recorded_says_so_plainly():
    lines = measure([], until=at(100))
    assert find(lines, "Median seconds to answer").value == "-"
    assert (
        find(lines, "Average ease (1 to 5)").based_on == "Not collected yet: the 2-weekly survey is not built"
    )


def test_the_reports_page_and_download_show_the_measures(client, make_user, grant, db):
    import uuid

    from django.utils import timezone

    from apps.calls.models import Event

    now = timezone.now()
    for seconds in (4, 8):
        Event.objects.create(
            event_id=uuid.uuid4(), type="offer.answered", at=now, data={"agent": "a", "seconds": seconds}
        )
    manager = make_user(account_type="Internal", is_staff=True)
    grant(manager, "can_view_reports")
    client.force_login(manager)
    html = client.get("/admin/reports/report/").content.decode()
    assert "How well AlgoDial works for its users" in html and "Median seconds to answer" in html
    assert "6.0 s" in html and "2 offers answered" in html
    csv = client.get("/admin/reports/report/export/experience.csv").content.decode()
    assert "Median seconds to answer" in csv and "Missed offers per 100" in csv
