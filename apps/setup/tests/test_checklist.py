"""The setup checklist reads the real configuration and the call records, and says in plain words
what is done, what needs attention (with the names of what is wrong) and what is not done yet."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.agents import services as agents
from apps.agents.models import NotReadyReason, Skill, WrapupCode
from apps.ai.models import AiAgent, AiRoute
from apps.calls.models import Call, InteractionPart
from apps.queues import services as queues
from apps.setup import checklist, services
from apps.teams import services as teams

pytestmark = pytest.mark.django_db

RUNNING = {"brain": 1000, "writer": 1000, "ai": 1000}  # each service's last check-in, in ms


def check(key, **kwargs):
    return checklist.find(checklist.build(consumers=RUNNING, **kwargs), key)


def problems(item):
    return " | ".join(line.text for line in item.problems)


@pytest.fixture
def centre(make_user, grant):
    """A small working centre: agent 8003 (a person who can sign in) in Support, on 800, in a team."""
    person = make_user(email="agent8003@example.org", first_name="Amina")
    grant(person, "can_take_calls")
    boss = make_user(email="boss@example.org")
    grant(boss, "can_monitor_calls")
    agent = agents.create_agent(actor=None, number="8003", name="Amina", user=person)
    support = queues.save_queue(actor=None, name="Support", agents=[agent])
    queues.save_entry_point(actor=None, number="800", queue=support, name="Support")
    teams.create_team(actor=None, name="Lab", supervisors=[boss], agents=[person])
    WrapupCode.objects.create(name="Sale")
    NotReadyReason.objects.create(name="Lunch")
    return {"person": person, "agent": agent, "support": support, "boss": boss}


def test_an_empty_centre_says_where_to_start():
    assert check("entries.exist").status == checklist.TODO
    assert check("queues.members").status == checklist.TODO
    assert check("tests.answered").status == checklist.TODO


def test_a_working_centre_is_done(centre):
    for key in (
        "people.sign_in",
        "people.agent_role",
        "people.supervisor_role",
        "agents.in_queue",
        "agents.wrapup_codes",
        "agents.not_ready_reasons",
        "queues.members",
        "entries.exist",
        "entries.reach_algodial",
        "teams.complete",
        "teams.every_agent",
        "services.brain",
        "services.writer",
    ):
        assert check(key).status == checklist.DONE, (key, problems(check(key)))
    assert check("entries.ai").status == checklist.NOT_USED, "no number has an AI agent"
    assert check("services.ai").status == checklist.NOT_USED


def test_a_user_made_in_the_admin_is_not_verified_and_cannot_sign_in(centre):
    """The admin's Add user form leaves "Is verified" off: the agent page then refuses the sign-in."""
    person = centre["person"]
    person.is_verified = False
    person.save()
    item = check("people.sign_in")
    assert item.status == checklist.ATTENTION
    assert "8003 Amina" in problems(item) and "Is verified" in problems(item)
    assert item.problems[0].url.endswith(f"/admin/users/user/{person.pk}/change/")

    person.is_verified, person.must_change_password = True, True
    person.save()
    assert "Must change password" in problems(check("people.sign_in"))


def test_an_agent_without_the_agent_role_is_named(centre, make_user):
    other = make_user(email="norole@example.org")
    agents.create_agent(actor=None, number="8004", name="No Role", user=other)
    item = check("people.agent_role")
    assert item.status == checklist.ATTENTION and "8004 No Role" in problems(item)


def test_an_agent_without_a_user_is_only_a_note(centre):
    """Scripted test agents (9001, 9002) have no user on purpose; a person needs one to sign in."""
    agents.create_agent(actor=None, number="9001", name="Lab agent A")
    item = check("people.sign_in")
    assert item.status == checklist.DONE
    assert "9001 Lab agent A" in " ".join(line.text for line in item.notes)


def test_a_queue_with_nobody_and_an_agent_in_no_queue(centre):
    queues.save_queue(actor=None, name="Claims")
    loose = agents.create_agent(actor=None, number="8005", name="Loose")
    assert "Claims" in problems(check("queues.members"))
    assert "8005 Loose" in problems(check("agents.in_queue"))
    assert loose  # used above


def test_nobody_in_the_queue_meets_its_last_skill_step(centre):
    swahili = Skill.objects.create(name="swahili", kind="language")
    rules = queues.save_skill_rule_set(actor=None, name="R_SUPPORT", steps=[(None, {"$language": 6})])
    support = centre["support"]
    queues.save_queue(actor=None, uid=support.uid, name="Support", skill_rule_set=rules)
    entry = support.entry_points.get(number="800")
    entry.language = swahili
    entry.save()
    item = check("queues.skills")
    assert item.status == checklist.ATTENTION
    assert "Support" in problems(item) and "swahili 6" in problems(item)

    agent = centre["agent"]
    agent.skills.create(skill=swahili, level=7)
    assert check("queues.skills").status == checklist.DONE


def test_a_number_asterisk_never_sends_to_algodial(centre):
    queues.save_entry_point(actor=None, number="7001", queue=centre["support"])
    item = check("entries.reach_algodial")
    assert item.status == checklist.ATTENTION and "7001" in problems(item)


def test_an_ai_number_needs_a_key_and_routes(centre, settings):
    ai = AiAgent.objects.create(name="Jambo AI", instructions="A helpline.")
    entry = centre["support"].entry_points.get(number="800")
    entry.ai_agent = ai
    entry.save()
    settings.OPENAI_API_KEY = ""
    item = check("entries.ai")
    assert item.status == checklist.ATTENTION
    assert "OPENAI_API_KEY" in problems(item) and "no routes" in problems(item)

    settings.OPENAI_API_KEY = "sk-test"
    AiRoute.objects.create(ai_agent=ai, queue=centre["support"], when="anything")
    assert check("entries.ai").status == checklist.DONE


def test_a_team_without_a_supervisor_and_an_agent_in_no_team(centre, make_user):
    teams.create_team(actor=None, name="Night")
    person = make_user(email="loner@example.org")
    agents.create_agent(actor=None, number="8006", name="Loner", user=person)
    assert "Night" in problems(check("teams.complete"))
    assert "8006 Loner" in problems(check("teams.every_agent"))


def test_a_stopped_service_needs_attention():
    sections = checklist.build(consumers={"brain": 90_000, "writer": None, "ai": 1000})
    brain, writer = checklist.find(sections, "services.brain"), checklist.find(sections, "services.writer")
    assert brain.status == checklist.ATTENTION and "make dev" in brain.summary
    assert writer.status == checklist.ATTENTION


def test_redis_out_of_reach_is_said_plainly(monkeypatch):
    monkeypatch.setattr(checklist, "read_consumers", lambda: None)
    item = checklist.find(checklist.build(), "services.brain")
    assert item.status == checklist.ATTENTION and "Redis" in item.summary


def test_tests_are_ticked_from_the_call_records(centre):
    now = timezone.now()
    assert "800" in problems(check("tests.answered")) or check("tests.answered").status == checklist.TODO
    Call.objects.create(
        linkedid="t1",
        entry="800",
        started_at=now,
        answered_at=now,
        result="answered",
        recording_status="ready",
    )
    item = check("tests.answered")
    assert item.status == checklist.DONE, problems(item)
    assert check("tests.recording").status == checklist.DONE

    old = now - timedelta(days=60)
    Call.objects.filter(linkedid="t1").update(started_at=old, answered_at=old)
    assert check("tests.answered").status == checklist.TODO, "a test from two months ago proves nothing now"


def test_the_ai_handoff_test(centre, settings):
    settings.OPENAI_API_KEY = "sk-test"
    ai = AiAgent.objects.create(name="Jambo AI", instructions="A helpline.")
    queues.save_entry_point(actor=None, number="803", queue=centre["support"])
    from apps.queues.models import EntryPoint

    EntryPoint.objects.filter(number="803").update(ai_agent=ai)
    assert check("tests.ai_handoff").status == checklist.TODO
    call = Call.objects.create(linkedid="ai1", entry="803", started_at=timezone.now(), result="answered")
    InteractionPart.objects.create(call=call, kind="ai", started_at=timezone.now(), outcome="handoff")
    assert check("tests.ai_handoff").status == checklist.DONE


def test_a_hands_on_test_is_confirmed_and_cleared_by_a_person(centre, make_user):
    manager = make_user(email="manager@example.org", first_name="Tostao")
    assert check("confirm.audio").status == checklist.TODO
    services.confirm(actor=manager, check="confirm.audio", note="Clear both ways from 1001")
    item = check("confirm.audio")
    assert item.status == checklist.DONE and "Tostao" in item.summary
    services.clear(actor=manager, check="confirm.audio")
    assert check("confirm.audio").status == checklist.TODO


def test_only_known_hands_on_tests_can_be_confirmed(make_user):
    from apps.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        services.confirm(actor=make_user(), check="people.sign_in")


def test_progress_counts_what_is_done(centre):
    sections = checklist.build(consumers=RUNNING)
    done, total, attention = checklist.progress(sections)
    assert 0 < done < total and attention == 0


def test_an_old_hand_made_web_phone_is_named(centre):
    from apps.telephony.models import SipAccount

    SipAccount.objects.create(
        sip_id="web8003", kind="SOFTPHONE", password_encrypted="x", codecs="ulaw", context="lab"
    )
    item = check("phones.softphones")
    assert item.status == checklist.ATTENTION and "web8003 has a browser phone's name" in problems(item)


def test_an_agents_switched_off_desk_phone_is_named(centre):
    from apps.telephony import services as telephony

    desk = telephony.create_sip_account(
        actor=None, sip_id="1001", kind="SOFTPHONE", password="Long-enough-secret-1"
    )
    agents.update_agent(uid=centre["agent"].uid, actor=None, phone=desk)
    assert check("phones.agents").status == checklist.DONE
    assert "8003 on 1001" in check("phones.agents").summary
    telephony.set_sip_account_active(uid=desk.uid, actor=None, active=False)
    item = check("phones.agents")
    assert item.status == checklist.ATTENTION and "8003 Amina takes calls on phone 1001" in problems(item)


def test_carriers_are_checked(centre, monkeypatch):
    from apps.telephony import trunks

    monkeypatch.setattr(trunks, "reload_registrations", lambda: None)
    assert check("phones.carriers").status == checklist.NOT_USED
    queues.save_entry_point(actor=None, number="+255222123456", queue=centre["support"])
    item = check("phones.carriers")
    assert item.status == checklist.ATTENTION and "+255222123456 have no live trunk" in problems(item)
    trunk = trunks.save_trunk(actor=None, name="lab-carrier", preset="lab-sipp")
    item = check("phones.carriers")
    assert item.status == checklist.DONE and "lab-carrier is in no outbound route" in item.notes[0].text
    trunks.save_route(actor=None, name="Mobiles", patterns="+2557", trunks=[trunk])
    assert check("phones.carriers").summary == "1 trunk, 1 DID, 1 outbound route."
