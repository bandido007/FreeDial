"""Reports through the database, the admin page, the downloads and the API."""

import csv
import io
from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.agents.models import Agent
from apps.calls import writer
from apps.queues import services as queues
from apps.reports.permissions import VIEW_REPORTS
from apps.reports.selectors import build_report, period
from contracts import messages as m
from contracts.envelope import Event as Message

pytestmark = pytest.mark.django_db


@pytest.fixture
def a_day():
    """Today: one call to Support answered by agent 7001 after 8 s, one abandoned after 30 s."""
    queues.save_queue(actor=None, name="Support", service_level_seconds=20, service_level_percent=80)
    agent = Agent.objects.create(number="7001", name="Asha")
    uid = str(agent.uid)
    start = timezone.now() - timedelta(minutes=10)

    def write(seconds, kind, **data):
        writer.write(Message(kind, data, at=(start + timedelta(seconds=seconds)).isoformat()))

    write(
        0,
        m.AGENT_STATE,
        agent=uid,
        number="7001",
        availability="ready",
        activity="idle",
        reason="",
        call=None,
        endpoint="",
    )
    write(
        1, m.CALL_STARTED, call="c1", leg="c1", entry="800", queue="Support", caller="0712", sip_call_id="s"
    )
    write(1, m.CALL_QUEUED, call="c1", queue="Support", reason="entry")
    write(9, m.CALL_CONNECTED, call="c1", leg="a1", agent=uid)
    write(
        9,
        m.AGENT_STATE,
        agent=uid,
        number="7001",
        availability="ready",
        activity="talking",
        reason="",
        call="c1",
        endpoint="",
    )
    write(69, m.LEG_ENDED, call="c1", leg="a1", role="agent")
    write(69, m.CALL_ENDED, call="c1", result="answered", reason="agent hung up", agent=uid)
    write(
        69,
        m.AGENT_STATE,
        agent=uid,
        number="7001",
        availability="ready",
        activity="idle",
        reason="",
        call=None,
        endpoint="",
    )
    write(
        100, m.CALL_STARTED, call="c2", leg="c2", entry="800", queue="Support", caller="0713", sip_call_id="s"
    )
    write(100, m.CALL_QUEUED, call="c2", queue="Support", reason="entry")
    write(130, m.CALL_ENDED, call="c2", result="abandoned", reason="customer hung up")
    return uid


def test_the_report_reads_the_event_log(a_day):
    since, until = period("today")
    report = build_report(since=since, until=until)
    [support] = report.queues
    assert (support.offered, support.answered, support.abandoned, support.service_level_percent) == (
        2,
        1,
        1,
        50.0,
    )
    assert (support.average_speed_of_answer, support.average_talk) == (8.0, 60.0)
    [agent] = report.agents
    assert agent.name == "7001 Asha" and agent.answered == 1 and agent.seconds["talking"] == 60.0
    assert report.until <= timezone.now(), "a period ending in the future stops at now"


def test_periods():
    today = timezone.localdate()
    since, until = period("today")
    assert since.date() == today and until - since == timedelta(days=1)
    since, until = period("last_7_days")
    assert until - since == timedelta(days=7) and until.date() == today + timedelta(days=1)
    since, until = period("custom", date(2026, 10, 1), date(2026, 10, 3))
    assert (since.date(), until.date()) == (date(2026, 10, 1), date(2026, 10, 4)), "both days included"
    with pytest.raises(ValueError):
        period("custom", date(2026, 10, 3), date(2026, 10, 1))


def test_the_admin_page_and_its_downloads(client, make_user, grant, a_day):
    page = "/admin/reports/report/"
    stranger = make_user(account_type="Internal", is_staff=True)
    client.force_login(stranger)
    assert client.get(page).status_code == 403
    manager = make_user(account_type="Internal", is_staff=True)
    grant(manager, VIEW_REPORTS)
    client.force_login(manager)
    html = client.get(page).content.decode()
    assert "Calls per queue" in html and "Agent time in state" in html and "AI agents" in html
    assert "Support" in html and "50.0% (missed)" in html and "7001 Asha" in html

    response = client.get(f"{page}export/queues.csv?period=today")
    assert response.status_code == 200 and "attachment" in response["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    header = next(i for i, row in enumerate(rows) if row and row[0] == "Queue")
    assert rows[header + 1][:3] == ["Support", "2", "1"]
    assert client.get(f"{page}export/secrets.csv").status_code == 404
    assert client.get(f"{page}export/queues.exe").status_code == 404
    assert client.get(f"{page}?period=custom&since=nonsense").status_code == 200, "a bad date shows today"


def test_the_api(gql, make_user, grant, a_day):
    query = 'query { getReport(period: "today") { status code data { queues { queue offered answered serviceLevelPercent } agents { name seconds } ai { name } } } }'
    user = make_user(account_type="Internal")
    assert gql(query, user=user)["status"] is False
    grant(user, VIEW_REPORTS)
    answer = gql(query, user=user)
    assert answer["code"] == 9001
    assert answer["data"]["queues"] == [
        {"queue": "Support", "offered": 2, "answered": 1, "serviceLevelPercent": 50.0}
    ]
    assert answer["data"]["agents"][0]["seconds"]["talking"] == 60.0
    bad = gql('query { getReport(period: "custom") { status code } }', user=user)
    assert (bad["status"], bad["code"]) == (False, 10900)
    export = gql(
        'query { exportReport(section: "agents", format: CSV) { code data { fileName contentBase64 } } }',
        user=user,
    )
    assert export["code"] == 9001 and export["data"]["fileName"].endswith(".csv")
    unknown = gql('query { exportReport(section: "nope", format: CSV) { code } }', user=user)
    assert unknown["code"] == 10901
