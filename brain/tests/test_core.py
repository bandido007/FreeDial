"""The core alone: plain function calls, no event loop, no fakes.

Decisions are pure, so the same inputs always give the same actions.
"""

import itertools

from brain.core import actions as a
from brain.core.agents import Activity, Availability
from brain.core.brain import Brain, stasis_args
from brain.tests.harness import PHONE_A, PHONE_B, A, B, make_config
from contracts import messages as m


def new_brain():
    ids = itertools.count(1)
    return Brain(make_config(), app="algodial", new_id=lambda: f"id{next(ids)}")


def stasis_start(cid, *args):
    return {
        "type": "StasisStart",
        "args": list(args),
        "channel": {
            "id": cid,
            "protocol_id": f"sip-{cid}",
            "caller": {"name": "Customer", "number": "0700"},
            "dialplan": {"app_name": "Stasis", "app_data": ",".join(["algodial", *args])},
        },
    }


def ready(brain, uid, phone, now):
    brain.on_command(m.AGENT_LOGIN, {"agent": uid, "endpoint": phone}, "c-login", now)
    return brain.on_command(m.AGENT_READY, {"agent": uid}, "c-ready", now)


def of(kind, actions):
    return [action for action in actions if isinstance(action, kind)]


def test_an_inbound_call_is_answered_bridged_with_music_and_rings_the_longest_idle_agent():
    brain = new_brain()
    ready(brain, B, PHONE_B, now=10.0)
    ready(brain, A, PHONE_A, now=20.0)  # B has been idle longer
    actions = brain.on_ari(stasis_start("c1", "inbound", "800", "lab-customer", "c1"), now=30.0)

    assert [type(action).__name__ for action in actions if isinstance(action, a.AriAction)] == [
        "Answer",
        "CreateBridge",
        "AddToBridge",
        "StartMoh",
        "Originate",
    ]
    originate = of(a.Originate, actions)[0]
    assert (originate.endpoint, originate.originator, originate.app_args) == (PHONE_B, "c1", f"agent,c1,{B}")
    assert originate.timeout == 30 and originate.caller_id == '"Customer" <0700>'
    assert of(a.SetTimer, actions)[0].seconds == 60
    assert brain.agents[B].activity is Activity.OFFERED


def test_the_linkedid_arg_names_the_call_and_old_dialplans_fall_back_to_the_channel_id():
    brain = new_brain()
    with_linkedid = brain.on_ari(stasis_start("c1", "inbound", "800", "x", "linked-1"), now=1.0)
    without = brain.on_ari(stasis_start("c2", "inbound", "800", "x"), now=1.0)
    assert of(a.Emit, with_linkedid)[0].data["call"] == "linked-1"
    assert of(a.Emit, without)[0].data["call"] == "c2"


def test_decisions_are_repeatable():
    first, second = new_brain(), new_brain()
    for brain in (first, second):
        ready(brain, A, PHONE_A, now=1.0)
    event = stasis_start("c1", "inbound", "800", "x", "c1")
    assert first.on_ari(event, now=2.0) == second.on_ari(event, now=2.0)


def test_commands_are_idempotent():
    brain = new_brain()
    assert ready(brain, A, PHONE_A, now=1.0)
    assert ready(brain, A, PHONE_A, now=2.0) == []  # the same requests again: nothing changes
    assert brain.agents[A].idle_since == 1.0


def test_logging_out_or_moving_phone_during_a_call_is_refused():
    brain = new_brain()
    ready(brain, A, PHONE_A, now=1.0)
    brain.on_ari(stasis_start("c1", "inbound", "800", "x", "c1"), now=2.0)
    refused = brain.on_command(m.AGENT_LOGOUT, {"agent": A}, "c-out", now=3.0)
    assert of(a.Emit, refused)[0].data == {
        "command_id": "c-out",
        "type": m.AGENT_LOGOUT,
        "reason": "the agent is on a call",
    }
    moved = brain.on_command(m.AGENT_LOGIN, {"agent": A, "endpoint": PHONE_B}, "c-move", now=3.0)
    assert of(a.Emit, moved)[0].type == m.COMMAND_REJECTED
    assert brain.agents[A].availability is Availability.READY


def test_not_ready_keeps_the_agent_off_calls_and_clears_not_responding():
    brain = new_brain()
    ready(brain, A, PHONE_A, now=1.0)
    brain.agents[A].activity = Activity.NOT_RESPONDING
    brain.on_command(m.AGENT_NOT_READY, {"agent": A, "reason": "break"}, "c", now=2.0)
    assert (brain.agents[A].availability, brain.agents[A].activity) == (Availability.NOT_READY, Activity.IDLE)
    assert not of(a.Originate, brain.on_ari(stasis_start("c1", "inbound", "800", "x", "c1"), now=3.0))


def test_reconcile_with_nothing_in_asterisk_and_nothing_in_memory_does_nothing_but_report():
    actions = new_brain().reconcile([], [], connected_at=1.0, now=1.0)
    assert actions == [a.Emit(m.BRAIN_RECONCILED, {"adopted": [], "hung_up": [], "bridges_deleted": []})]


def test_reconcile_leaves_alone_channels_created_after_connecting():
    channel = stasis_start("new", "inbound", "800", "x", "new")["channel"] | {
        "creationtime": "2026-10-14T04:10:59.569+0300"
    }
    actions = new_brain().reconcile([channel], [], connected_at=channel_time(channel), now=2.0)
    assert not of(a.Hangup, actions) and not of(a.Answer, actions)


def test_reconcile_takes_a_customer_who_never_got_a_decision():
    channel = stasis_start("old", "inbound", "800", "x", "old")["channel"] | {
        "creationtime": "2026-10-14T04:10:59.569+0300"
    }
    actions = new_brain().reconcile([channel], [], connected_at=channel_time(channel) + 5, now=2.0)
    assert of(a.Answer, actions) and of(a.Emit, actions)[-1].data["adopted"] == ["old"]


def test_stasis_args_reads_only_our_app():
    ours = {"dialplan": {"app_name": "Stasis", "app_data": "algodial,inbound,800"}}
    other = {"dialplan": {"app_name": "Stasis", "app_data": "otherapp,inbound,800"}}
    dialling = {"dialplan": {"app_name": "AppDial2", "app_data": "(Outgoing Line)"}}
    assert stasis_args(ours, "algodial") == ["inbound", "800"]
    assert stasis_args(other, "algodial") is None and stasis_args(dialling, "algodial") is None


def channel_time(channel):
    from brain.core.brain import parse_time

    return parse_time(channel["creationtime"])
