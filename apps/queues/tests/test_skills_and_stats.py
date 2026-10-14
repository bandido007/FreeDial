"""M4 in the admin's world: skills, skill rule sets, queue settings and service-level stats."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.agents import services as agents
from apps.agents.models import AgentSkill
from apps.calls import writer
from apps.calls.models import Call
from apps.core.exceptions import DuplicateError, ValidationError
from apps.queues import services as queues
from contracts import messages as m
from contracts.envelope import Event

pytestmark = pytest.mark.django_db


@pytest.fixture
def skills():
    return {name: agents.create_skill(actor=None, name=name) for name in ("swahili", "english", "billing")}


def test_skill_names_are_what_rules_can_name(skills):
    with pytest.raises(ValidationError):
        agents.create_skill(actor=None, name="Customer Care")
    with pytest.raises(ValidationError):
        agents.create_skill(actor=None, name="true")
    with pytest.raises(DuplicateError):
        agents.create_skill(actor=None, name="SWAHILI")


def test_an_agent_gets_skill_levels_from_1_to_10(skills):
    agent = agents.create_agent(actor=None, number="7001", name="Asha")
    agents.set_agent_skills(agent=agent, levels={"swahili": 9, "billing": 4})
    assert dict(AgentSkill.objects.filter(agent=agent).values_list("skill__name", "level")) == {
        "swahili": 9,
        "billing": 4,
    }
    agents.set_agent_skills(agent=agent, levels={"swahili": 7})
    assert dict(AgentSkill.objects.filter(agent=agent).values_list("skill__name", "level")) == {"swahili": 7}
    for bad in ({"swahili": 11}, {"swahili": 0}, {"french": 5}):
        with pytest.raises(ValidationError):
            agents.set_agent_skills(agent=agent, levels=bad)


def test_a_rule_set_holds_valid_conditions_on_known_skills(skills):
    rule_set = queues.save_skill_rule_set(
        actor=None, name="Swahili first", rules=[(0, "swahili >= 8"), (30, "swahili >= 5"), (60, "true")]
    )
    assert [(rule.after_seconds, rule.condition) for rule in rule_set.rules.all()] == [
        (0, "swahili >= 8"),
        (30, "swahili >= 5"),
        (60, "true"),
    ]
    for rules in ([(0, "swahili >=")], [(0, "french > 2")], [(0, "true"), (0, "billing")], [(-5, "true")]):
        with pytest.raises(ValidationError):
            queues.save_skill_rule_set(actor=None, name="Bad", rules=rules)


def test_a_queue_cannot_overflow_into_itself():
    queue = queues.save_queue(actor=None, name="Support")
    with pytest.raises(ValidationError):
        queues.validate_overflow(queue, queue)


def test_the_admin_pages_for_skills_and_rules_open(admin):
    browser = Client()
    browser.force_login(admin)
    queue = queues.save_queue(actor=None, name="Support")
    for page in (
        "/admin/agents/skill/add/",
        "/admin/queues/skillruleset/add/",
        f"/admin/queues/queue/{queue.pk}/change/",
        "/admin/queues/queue/",
    ):
        assert browser.get(page).status_code == 200, page
    form = browser.get(f"/admin/queues/queue/{queue.pk}/change/").content.decode()
    assert all(
        field in form for field in ('name="strategy"', 'name="overflow_queue"', 'name="skill_rule_set"')
    )


def test_the_writer_follows_a_call_into_its_overflow_queue():
    writer.write(
        Event(
            m.CALL_STARTED,
            {"call": "c1", "leg": "c1", "entry": "800", "queue": "Support", "caller": "x", "sip_call_id": ""},
        )
    )
    writer.write(
        Event(
            m.CALL_OVERFLOWED,
            {"call": "c1", "from_queue": "Support", "to_queue": "Backup", "reason": "max wait reached"},
        )
    )
    assert Call.objects.get(linkedid="c1").queue == "Backup"


def make_call(queue, *, started, waited=None, talked=60, result="answered"):
    answered = started + timedelta(seconds=waited) if waited is not None else None
    ended = (answered or started) + timedelta(seconds=talked if answered else 30)
    return Call.objects.create(
        linkedid=f"c{Call.objects.count() + 1}",
        queue=queue,
        started_at=started,
        answered_at=answered,
        ended_at=ended,
        result=result,
    )


def test_service_level_counts_calls_answered_within_the_target_of_all_offered(gql, make_user, grant):
    queue = queues.save_queue(actor=None, name="Support", service_level_seconds=20, service_level_percent=80)
    now = timezone.now()
    for waited in (5, 10, 20, 40):  # three within 20 s
        make_call("Support", started=now - timedelta(minutes=30), waited=waited)
    make_call("Support", started=now - timedelta(minutes=20), result="abandoned")
    make_call("Support", started=now - timedelta(minutes=10), result="overflow")
    make_call("Other", started=now - timedelta(minutes=10), waited=1)
    make_call("Support", started=now - timedelta(days=3), waited=1)  # outside the period

    viewer = make_user(account_type="Internal")
    grant(viewer, "can_view_queues")
    answer = gql(
        "query S($uid: UUID!, $since: DateTime) { getQueueStats(uid: $uid, since: $since) { code data {"
        " offered answered abandoned overflowed answeredWithinTarget serviceLevelPercent targetPercent metTarget"
        " averageWaitSeconds longestWaitSeconds averageTalkSeconds } } }",
        {"uid": str(queue.uid), "since": (now - timedelta(hours=1)).isoformat()},
        user=viewer,
    )
    stats = answer["data"]
    assert (stats["offered"], stats["answered"], stats["abandoned"], stats["overflowed"]) == (6, 4, 1, 1)
    assert stats["answeredWithinTarget"] == 3
    assert (
        stats["serviceLevelPercent"] == 50.0 and stats["targetPercent"] == 80 and stats["metTarget"] is False
    )
    assert (stats["averageWaitSeconds"], stats["longestWaitSeconds"], stats["averageTalkSeconds"]) == (
        18.8,
        40.0,
        60.0,
    )


def test_stats_need_permission(gql, user):
    queue = queues.save_queue(actor=None, name="Support")
    answer = gql(
        "query S($uid: UUID!) { getQueueStats(uid: $uid) { code } }", {"uid": str(queue.uid)}, user=user
    )
    assert answer["code"] == 9000
