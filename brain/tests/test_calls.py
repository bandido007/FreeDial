"""M2: the brain against a fake Asterisk, through the call tests of docs/POC_PLAN.md.

Each test ends the way the lab checks a real call: no channel and no bridge left.
Test 9 (brain stopped, the dialplan's Queue() answers) is the dialplan's job and is
run on the real lab by lab/calltests.py.
"""

import asyncio

from brain.core.brain import AGENT_LEG_PREFIX, BRIDGE_PREFIX
from brain.sim import FakeAri, FakeBus
from brain.tests.harness import PHONE_A, PHONE_B, A, B, brain, eventually, make_config, scenario
from contracts import messages as m
from contracts.envelope import Command


# ── 1 and 2: a normal call ends cleanly whoever hangs up ──────────────────
@scenario
async def test_1_customer_hangs_up_after_talking():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        customer, leg = await lab.connected_call()

        bridge = lab.sim.bridge_of(customer)
        assert bridge["name"] == BRIDGE_PREFIX + customer
        assert set(bridge["channels"]) == {customer, leg}
        assert bridge["id"] not in lab.sim.moh, "the music stops when the agent joins"

        lab.sim.hangup(customer)
        await lab.all_clear()
        assert lab.ended()[-1]["result"] == "answered"
        assert lab.agent(A)["activity"] == "idle"


@scenario
async def test_2_agent_hangs_up():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        _customer, leg = await lab.connected_call()
        lab.sim.hangup(leg)
        await lab.all_clear()
        ended = lab.ended()[-1]
        assert (ended["result"], ended["reason"], ended["agent"]) == ("answered", "agent hung up", A)


# ── 3: the agent's phone stops ringing at once ────────────────────────────
@scenario
async def test_3_customer_hangs_up_while_the_agent_rings():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        customer = lab.sim.call_in("800")
        leg = await lab.sim.next_ringing()
        assert lab.sim.channels[leg]["state"] == "Ringing"

        lab.sim.hangup(customer)
        await eventually(lambda: leg not in lab.sim.channels, timeout=0.5, what="agent phone stopped ringing")
        await lab.all_clear()
        assert lab.ended()[-1]["result"] == "abandoned"
        assert lab.agent(A)["activity"] == "idle" and lab.agent(A)["availability"] == "ready"


# ── 4, 5, 6: when the first agent does not take it, the next one rings ────
@scenario
async def test_4_agent_rejects_and_the_next_agent_rings():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        await lab.ready(B, PHONE_B)  # A has been idle longest: A rings first
        lab.sim.call_in("800")
        first = await lab.sim.next_ringing()
        assert lab.sim.channels[first]["name"].startswith(PHONE_A)

        lab.sim.reject(first, cause=17)
        second = await lab.sim.next_ringing()
        assert lab.sim.channels[second]["name"].startswith(PHONE_B)
        failed = lab.bus.of_type(m.CALL_OFFER_FAILED)[-1]
        assert (failed["leg"], failed["agent"], failed["reason"], failed["cause"]) == (
            first,
            A,
            "rejected",
            17,
        )
        assert lab.agent(A)["activity"] == "idle", "rejecting is not a fault: A stays Ready"

        lab.sim.answer(second)
        await eventually(lambda: lab.agent(B).get("activity") == "talking")
        lab.sim.hangup(second)
        await lab.all_clear()


@scenario
async def test_5_agent_does_not_answer_becomes_not_responding():
    async with brain(config=make_config(ring_timeout=0.2)) as lab:
        await lab.ready(A, PHONE_A)
        await lab.ready(B, PHONE_B)
        lab.sim.call_in("800")
        first = await lab.sim.next_ringing()  # A's phone rings out after 0.2 s (cause 19)
        second = await lab.sim.next_ringing()
        assert lab.sim.channels[second]["name"].startswith(PHONE_B)
        assert lab.agent(A)["activity"] == "not_responding"
        assert lab.bus.of_type(m.CALL_OFFER_FAILED)[-1]["reason"] == "no_answer"
        assert first not in lab.sim.channels

        lab.sim.answer(second)
        await eventually(lambda: lab.agent(B).get("activity") == "talking")
        lab.sim.hangup(second)
        await lab.all_clear()

        # Not responding means offered nothing until the agent says Ready again.
        lab.sim.call_in("800")
        assert (await lab.sim.next_ringing()) and lab.sim.originates()[-1]["endpoint"] == PHONE_B
        await lab.command(m.AGENT_READY, agent=A)
        await eventually(lambda: lab.agent(A).get("activity") == "idle")


@scenario
async def test_6_agent_phone_offline_fails_cleanly_and_the_next_agent_rings():
    async with brain() as lab:
        await lab.ready(A, PHONE_A, online=False)  # logged in, but no phone registered
        await lab.ready(B, PHONE_B)
        customer = lab.sim.call_in("800")
        leg = await lab.sim.next_ringing()
        assert lab.sim.channels[leg]["name"].startswith(PHONE_B)
        assert [o["endpoint"] for o in lab.sim.originates()] == [PHONE_A, PHONE_B]
        assert lab.bus.of_type(m.CALL_OFFER_FAILED)[-1]["reason"] == "offline"
        assert lab.agent(A)["activity"] == "not_responding"
        lab.sim.hangup(customer)
        await lab.all_clear()


# ── 7: every StasisStart ends in a decision ───────────────────────────────
@scenario
async def test_7_unknown_args_are_logged_and_hung_up():
    async with brain() as lab:
        odd = lab.sim.call_in(args=["bogus", "x"])
        empty = lab.sim.call_in(args=[])
        nowhere = lab.sim.call_in("801")  # args fine, but no entry point 801
        await lab.all_clear()
        rejected = {event["channel"]: event["reason"] for event in lab.bus.of_type(m.STASIS_REJECTED)}
        assert rejected == {odd: "unknown args", empty: "unknown args", nowhere: "no entry point 801"}


@scenario
async def test_an_agent_answering_for_a_call_that_is_gone_is_hung_up():
    async with brain() as lab:
        lab.sim.registered.add(PHONE_A)
        await lab.sim.request(
            "POST",
            "/channels",
            endpoint=PHONE_A,
            app="algodial",
            appArgs="agent,no-such-call," + A,
            channelId="agent-x",
        )
        lab.sim.answer("agent-x")
        await lab.all_clear()
        assert lab.bus.of_type(m.STASIS_REJECTED)[-1]["reason"] == "its call is gone"


# ── 8: the brain dies mid-call and comes back ─────────────────────────────
@scenario
async def test_8_a_restarted_brain_adopts_a_connected_call():
    sim, bus = FakeAri(), FakeBus()
    async with brain(sim, bus) as lab:
        await lab.ready(A, PHONE_A)
        customer, leg = await lab.connected_call()
        call = lab.bus.of_type(m.CALL_CONNECTED)[-1]["call"]
    sim.disconnect()  # the old brain is gone; the call lives on in Asterisk

    async with brain(sim, bus) as lab:
        reconciled = lab.bus.of_type(m.BRAIN_RECONCILED)[-1]
        assert reconciled["adopted"] == [call] and reconciled["hung_up"] == []
        assert lab.agent(A)["activity"] == "talking"
        assert set(sim.bridge_of(customer)["channels"]) == {customer, leg}, "the call was not disturbed"

        lab.sim.hangup(customer)
        await lab.all_clear()
        assert lab.ended()[-1]["call"] == call
        assert lab.agent(A)["activity"] == "idle"


@scenario
async def test_8_a_restarted_brain_cleans_a_call_that_lost_its_customer():
    sim, bus = FakeAri(), FakeBus()
    async with brain(sim, bus) as lab:
        await lab.ready(A, PHONE_A)
        customer, leg = await lab.connected_call()
    sim.disconnect()
    sim.hangup(customer)  # while no brain listens

    async with brain(sim, bus) as lab:
        await lab.all_clear()
        assert lab.bus.of_type(m.BRAIN_RECONCILED)[-1]["hung_up"] == [leg]
        assert lab.agent(A)["activity"] == "idle", "the saved 'talking' state was fixed"
        ended = lab.ended()[-1]
        assert (ended["call"], ended["result"], ended["agent"]) == (customer, "answered", A)
        assert ended["reason"] == "cleaned after the brain restarted"


@scenario
async def test_8_a_restarted_brain_requeues_a_waiting_customer():
    sim, bus = FakeAri(), FakeBus()
    async with brain(sim, bus) as lab:
        customer = lab.sim.call_in("800")  # nobody Ready: the customer waits with music
        await eventually(lambda: sim.bridge_of(customer) is not None)
    sim.disconnect()

    async with brain(sim, bus) as lab:
        assert lab.bus.of_type(m.BRAIN_RECONCILED)[-1]["adopted"]
        await lab.ready(A, PHONE_A)
        leg = await lab.sim.next_ringing()
        assert lab.sim.originates()[-1]["originator"] == customer
        lab.sim.answer(leg)
        await eventually(lambda: lab.agent(A).get("activity") == "talking")
        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_a_lost_connection_is_reconciled_on_reconnect():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        customer = lab.sim.call_in("800")
        leg = await lab.sim.next_ringing()
        lab.sim.disconnect()
        lab.sim.hangup(customer)  # missed: nobody listens right now
        await eventually(lambda: lab.runtime.reconciled == 2, what="reconnected and reconciled")
        await lab.all_clear()
        assert leg not in lab.sim.channels
        assert lab.ended()[-1]["result"] == "abandoned"


@scenario
async def test_orphans_from_an_old_brain_are_cleaned():
    sim, bus = FakeAri(), FakeBus()
    sim.registered.add(PHONE_A)
    await sim.request("POST", "/bridges", type="mixing", bridgeId="old", name=BRIDGE_PREFIX + "old")
    await sim.request(
        "POST",
        "/channels",
        endpoint=PHONE_A,
        app="algodial",
        appArgs="agent,old," + A,
        channelId=AGENT_LEG_PREFIX + "x",
    )
    async with brain(sim, bus) as lab:
        await lab.all_clear()


# ── the rest of the rules ─────────────────────────────────────────────────
@scenario
async def test_every_agent_leg_carries_the_callers_channel_as_originator():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        customer, _leg = await lab.connected_call()
        assert lab.sim.originates()[-1]["originator"] == customer  # rule 4: one linkedid
        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_a_caller_waits_until_an_agent_is_ready():
    async with brain() as lab:
        customer = lab.sim.call_in("800")
        await eventually(lambda: lab.sim.bridge_of(customer) is not None)
        assert lab.sim.bridge_of(customer)["id"] in lab.sim.moh, "the caller hears music"
        assert lab.sim.channels[customer]["state"] == "Up"
        await lab.ready(A, PHONE_A)
        leg = await lab.sim.next_ringing()
        lab.sim.answer(leg)
        await eventually(lambda: lab.agent(A).get("activity") == "talking")
        lab.sim.hangup(leg)
        await lab.all_clear()


@scenario
async def test_max_wait_overflows_the_call():
    async with brain(config=make_config(max_wait=0.2)) as lab:
        lab.sim.call_in("800")
        await lab.all_clear()
        assert lab.ended()[-1]["result"] == "overflow"


@scenario
async def test_agents_who_refused_are_asked_again_after_the_retry_delay():
    async with brain(config=make_config(retry_delay=0.1)) as lab:
        await lab.ready(A, PHONE_A)
        customer = lab.sim.call_in("800")
        lab.sim.reject(await lab.sim.next_ringing())
        again = await lab.sim.next_ringing()
        assert lab.sim.channels[again]["name"].startswith(PHONE_A)
        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_cleanup_goes_on_when_one_step_fails():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        customer, leg = await lab.connected_call()
        lab.sim.failures[("DELETE", f"/channels/{leg}")] = 500  # the agent's hangup fails once
        lab.sim.hangup(customer)
        await eventually(lambda: not lab.sim.bridges, what="bridge deleted anyway")
        assert leg in lab.sim.channels, "the failed hangup left the agent leg"
        assert lab.ended()[-1]["result"] == "answered"


@scenario
async def test_a_failed_setup_ends_the_call_and_hangs_up_the_customer():
    async with brain() as lab:
        lab.sim.failures[("POST", "/bridges")] = 500
        lab.sim.call_in("800")
        await lab.all_clear()
        assert lab.ended()[-1]["result"] == "failed"


@scenario
async def test_a_command_delivered_twice_is_handled_once():
    async with brain() as lab:
        lab.sim.registered.add(PHONE_A)
        login = Command(type=m.AGENT_LOGIN, data={"agent": A, "endpoint": PHONE_A})
        await lab.bus.send(login)
        await lab.bus.send(login)
        await lab.command(m.AGENT_NOT_READY, agent=A, reason="lunch")
        await eventually(lambda: lab.agent(A).get("reason") == "lunch")
        states = [event for event in lab.bus.of_type(m.AGENT_STATE) if event["agent"] == A]
        assert [state["availability"] for state in states] == ["not_ready", "not_ready"]
        assert [state["reason"] for state in states] == ["login", "lunch"]


@scenario
async def test_commands_the_brain_cannot_carry_out_are_refused_with_a_reason():
    async with brain() as lab:
        unknown = await lab.command(m.AGENT_READY, agent="nobody")
        not_in = await lab.command(m.AGENT_READY, agent=A)
        await eventually(lambda: len(lab.bus.of_type(m.COMMAND_REJECTED)) == 2)
        refused = {event["command_id"]: event["reason"] for event in lab.bus.of_type(m.COMMAND_REJECTED)}
        assert refused == {
            unknown.command_id: "unknown agent",
            not_in.command_id: "the agent is not logged in",
        }
        assert all(event.command_id for event in lab.bus.events if event.type == m.COMMAND_REJECTED)


@scenario
async def test_an_old_command_is_refused_as_expired():
    async with brain() as lab:
        old = Command(
            type=m.AGENT_LOGIN, data={"agent": A, "endpoint": PHONE_A}, sent_at="2020-01-01T00:00:00+00:00"
        )
        await lab.bus.send(old)
        await eventually(lambda: lab.bus.of_type(m.COMMAND_REJECTED))
        assert lab.bus.of_type(m.COMMAND_REJECTED)[0]["reason"] == "expired"
        assert lab.agent(A) == {}


@scenario
async def test_config_changed_reloads_and_a_removed_agent_is_logged_out():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        smaller = make_config()
        del smaller.agents[A]

        async def load():
            return smaller

        lab.runtime.load_config = load
        await lab.command(m.CONFIG_CHANGED)
        await eventually(lambda: A not in lab.bus.agents, what="A forgotten")
        last = lab.bus.of_type(m.AGENT_STATE)[-1]
        assert (last["agent"], last["availability"]) == (A, "logged_out")


@scenario
async def test_redis_down_never_stops_a_call_and_nothing_is_lost():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        published = len(lab.bus.events)
        lab.bus.down = True
        customer = lab.sim.call_in("800")
        leg = await lab.sim.next_ringing()
        lab.sim.answer(leg)
        await eventually(lambda: lab.sim.bridge_of(leg) is not None, what="connected while Redis is down")
        lab.sim.hangup(customer)
        await eventually(lambda: not lab.sim.channels and not lab.sim.bridges)
        assert len(lab.bus.events) == published
        lab.bus.down = False
        await eventually(lambda: lab.ended(), timeout=4, what="held events published")
        types = [event.type for event in lab.bus.events[published:]]
        assert types.index(m.CALL_STARTED) < types.index(m.CALL_CONNECTED) < types.index(m.CALL_ENDED)


@scenario
async def test_every_event_follows_the_contract():
    async with brain(config=make_config(ring_timeout=0.2)) as lab:
        await lab.ready(A, PHONE_A)
        await lab.ready(B, PHONE_B)
        customer = lab.sim.call_in("800")
        await lab.sim.next_ringing()
        lab.sim.answer(await lab.sim.next_ringing())
        await eventually(lambda: lab.agent(B).get("activity") == "talking")
        lab.sim.hangup(customer)
        lab.sim.call_in(args=["bogus"])
        await lab.all_clear()
        await asyncio.sleep(0.05)
        for event in lab.bus.events:
            m.check(event)
            assert event.event_id and event.at
        assert {event.type for event in lab.bus.events} >= {
            m.CALL_STARTED,
            m.CALL_OFFERED,
            m.CALL_OFFER_FAILED,
            m.CALL_CONNECTED,
            m.CALL_ENDED,
            m.LEG_ENDED,
            m.AGENT_STATE,
            m.STASIS_REJECTED,
            m.BRAIN_RECONCILED,
        }


@scenario
async def test_with_no_brain_the_dialplan_fallback_takes_the_call():
    """Test 9 as the fake models it; the real check is lab/calltests.py against Asterisk."""
    sim = FakeAri()
    customer = sim.call_in("800")
    assert sim.fallbacks == [customer]
    assert sim.channels[customer]["dialplan"]["app_name"] == "Queue"


# ── M3: hold, resume and wrap-up ───────────────────────────────────────────
@scenario
async def test_hold_takes_the_agent_out_and_plays_music_resume_brings_them_back():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        customer, leg = await lab.connected_call()
        bridge = lab.sim.bridge_of(customer)["id"]

        await lab.command(m.CALL_HOLD, agent=A)
        await eventually(lambda: lab.agent(A).get("activity") == "on_hold")
        await eventually(lambda: lab.sim.bridges[bridge]["channels"] == [customer])
        assert bridge in lab.sim.moh and leg in lab.sim.channels, (
            "the agent waits in the app, outside the bridge"
        )

        await lab.command(m.CALL_HOLD, agent=A)  # again: nothing changes
        await lab.command(m.CALL_RESUME, agent=A)
        await eventually(lambda: lab.agent(A).get("activity") == "talking")
        await eventually(lambda: set(lab.sim.bridges[bridge]["channels"]) == {customer, leg})
        assert bridge not in lab.sim.moh
        assert [e.type for e in lab.bus.events if e.type in (m.CALL_HELD, m.CALL_RESUMED)] == [
            m.CALL_HELD,
            m.CALL_RESUMED,
        ]

        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_holding_without_a_call_is_refused():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        await lab.command(m.CALL_HOLD, agent=A)
        await eventually(lambda: lab.bus.of_type(m.COMMAND_REJECTED))
        refused = lab.bus.of_type(m.COMMAND_REJECTED)[0]
        assert (refused["reason"], refused["agent"]) == ("the agent has no connected call", A)


@scenario
async def test_a_customer_on_hold_who_hangs_up_ends_the_call_cleanly():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        customer, _leg = await lab.connected_call()
        await lab.command(m.CALL_HOLD, agent=A)
        await eventually(lambda: lab.agent(A).get("activity") == "on_hold")
        lab.sim.hangup(customer)
        await lab.all_clear()
        assert lab.agent(A)["activity"] == "idle"


@scenario
async def test_a_restarted_brain_adopts_a_call_on_hold():
    sim, bus = FakeAri(), FakeBus()
    async with brain(sim, bus) as lab:
        await lab.ready(A, PHONE_A)
        customer, leg = await lab.connected_call()
        await lab.command(m.CALL_HOLD, agent=A)
        await eventually(lambda: lab.agent(A).get("activity") == "on_hold")
    sim.disconnect()

    async with brain(sim, bus) as lab:
        assert lab.bus.of_type(m.BRAIN_RECONCILED)[-1]["hung_up"] == []
        assert lab.agent(A)["activity"] == "on_hold"
        await lab.command(m.CALL_RESUME, agent=A)
        await eventually(lambda: lab.sim.bridge_of(leg) is not None, what="agent back in the bridge")
        lab.sim.hangup(leg)
        await lab.all_clear()


@scenario
async def test_after_a_call_the_agent_wraps_up_with_a_code_then_gets_the_next_call():
    async with brain(config=make_config(wrapup_seconds=30)) as lab:
        await lab.ready(A, PHONE_A)
        customer, _leg = await lab.connected_call()
        lab.sim.hangup(customer)
        await eventually(lambda: lab.agent(A).get("activity") == "wrapup")
        call = lab.ended()[-1]["call"]
        assert lab.agent(A)["wrapup_call"] == call

        waiting = lab.sim.call_in("800")
        await eventually(lambda: lab.sim.bridge_of(waiting) is not None)
        assert not lab.sim.originates()[1:], "no offer during wrap-up"

        await lab.command(m.AGENT_WRAPUP_DONE, agent=A, code="Sale")
        leg = await lab.sim.next_ringing()
        wrapped = lab.bus.of_type(m.AGENT_WRAPPED_UP)[-1]
        assert wrapped == {"agent": A, "call": call, "code": "Sale", "by": "agent"}
        lab.sim.answer(leg)
        await eventually(lambda: lab.agent(A).get("activity") == "talking")
        lab.sim.hangup(waiting)
        await lab.all_clear()


@scenario
async def test_wrap_up_ends_by_itself_when_its_time_is_up():
    async with brain(config=make_config(wrapup_seconds=0.2)) as lab:
        await lab.ready(A, PHONE_A)
        customer, _leg = await lab.connected_call()
        lab.sim.hangup(customer)
        await eventually(lambda: lab.bus.of_type(m.AGENT_WRAPPED_UP), what="wrap-up timed out")
        assert lab.bus.of_type(m.AGENT_WRAPPED_UP)[-1]["by"] == "timeout"
        assert lab.agent(A)["activity"] == "idle"


@scenario
async def test_an_offer_tells_the_agent_who_is_calling():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        customer = lab.sim.call_in("800", caller="lab-customer")
        await lab.sim.next_ringing()
        offered = lab.bus.of_type(m.CALL_OFFERED)[-1]
        assert (offered["caller"], offered["caller_name"], offered["queue"]) == (
            "lab-customer",
            "lab-customer",
            "Support",
        )
        lab.sim.hangup(customer)
        await lab.all_clear()


# ── M4: ring all, through the real service ────────────────────────────────
@scenario
async def test_ring_all_stops_the_other_phones_when_one_answers():
    async with brain(config=make_config(strategy="ring_all")) as lab:
        await lab.ready(A, PHONE_A)
        await lab.ready(B, PHONE_B)
        customer = lab.sim.call_in("800")
        first, second = await lab.sim.next_ringing(), await lab.sim.next_ringing()
        lab.sim.answer(second)
        await eventually(lambda: first not in lab.sim.channels, timeout=0.5, what="the other phone stopped")
        await eventually(lambda: set(lab.sim.bridge_of(customer)["channels"]) == {customer, second})
        assert {lab.agent(A)["activity"], lab.agent(B)["activity"]} == {"idle", "talking"}
        lab.sim.hangup(customer)
        await lab.all_clear()
