"""M6: a supervisor monitors, whispers to and barges into a call, through the real service and the fake Asterisk.

Monitor: a snoop on the agent's channel (spy both, whisper none) shares a private bridge with the
supervisor's phone: the supervisor hears both sides, nobody hears the supervisor.
Whisper: the same with whisper out: only the agent hears the supervisor.
Barge: the supervisor's phone joins the call's bridge: everyone hears everyone.
"""

from brain.tests.harness import PHONE_A, PHONE_B, A, B, brain, eventually, scenario
from contracts import messages as m

SUP, SUP_PHONE = "supervisor-uid", "PJSIP/websup1"


async def supervised(lab, mode="monitor"):
    """A talks to a customer; the supervisor's phone is answered in ``mode``. Returns (customer, A's leg, sup leg)."""
    lab.sim.registered.add(SUP_PHONE)
    await lab.ready(A, PHONE_A)
    customer, a_leg = await lab.connected_call()
    call = lab.bus.of_type(m.CALL_CONNECTED)[-1]["call"]
    await lab.command(m.SUPERVISE_START, supervisor=SUP, endpoint=SUP_PHONE, call=call, mode=mode)
    sup_leg = await lab.sim.next_ringing()
    assert lab.sim.originates()[-1]["originator"] == customer, (
        "the supervisor's leg shares the call's linkedid"
    )
    lab.sim.answer(sup_leg)
    await eventually(lambda: lab.bus.of_type(m.SUPERVISE_STARTED), what="supervision started")
    return customer, a_leg, sup_leg


def bridge_with(lab, cid):
    bridge = lab.sim.bridge_of(cid)
    return set(bridge["channels"]) if bridge else set()


@scenario
async def test_monitor_hears_both_sides_and_nobody_hears_the_supervisor():
    async with brain() as lab:
        customer, a_leg, sup_leg = await supervised(lab, "monitor")
        await eventually(lambda: len(bridge_with(lab, sup_leg)) == 2, what="supervisor and snoop bridged")
        [snoop] = lab.sim.snoops()
        assert (snoop["target"], snoop["spy"], snoop["whisper"]) == (a_leg, "both", "none")
        assert bridge_with(lab, sup_leg) == {sup_leg, snoop["id"]}
        assert bridge_with(lab, customer) == {customer, a_leg}, "the call's own bridge is untouched"
        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_switching_monitor_whisper_barge_and_back():
    async with brain() as lab:
        customer, a_leg, sup_leg = await supervised(lab, "monitor")
        call = lab.bus.of_type(m.CALL_CONNECTED)[-1]["call"]
        await eventually(lambda: len(bridge_with(lab, sup_leg)) == 2)

        await lab.command(m.SUPERVISE_MODE, supervisor=SUP, mode="whisper")
        await eventually(
            lambda: [s["whisper"] for s in lab.sim.snoops()] == ["out"], what="one snoop, whispering"
        )
        await eventually(lambda: len(bridge_with(lab, sup_leg)) == 2)
        assert lab.sim.snoops()[0]["target"] == a_leg, "whisper goes to the agent's channel only"

        await lab.command(m.SUPERVISE_MODE, supervisor=SUP, mode="barge")
        await eventually(
            lambda: bridge_with(lab, customer) == {customer, a_leg, sup_leg}, what="supervisor in the call"
        )
        assert lab.sim.snoops() == [] and len(lab.sim.bridges) == 1

        await lab.command(m.SUPERVISE_MODE, supervisor=SUP, mode="monitor")
        await eventually(lambda: bridge_with(lab, customer) == {customer, a_leg})
        await eventually(lambda: [s["whisper"] for s in lab.sim.snoops()] == ["none"])
        modes = [event["mode"] for event in lab.bus.of_type(m.SUPERVISE_CHANGED)]
        assert (
            modes == ["whisper", "barge", "monitor"]
            and lab.bus.of_type(m.SUPERVISE_CHANGED)[0]["call"] == call
        )

        await lab.command(m.SUPERVISE_STOP, supervisor=SUP)
        await eventually(lambda: sup_leg not in lab.sim.channels and not lab.sim.snoops())
        assert len(lab.sim.bridges) == 1 and bridge_with(lab, customer) == {customer, a_leg}
        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_the_supervisor_hanging_up_leaves_the_call_alone():
    async with brain() as lab:
        customer, a_leg, sup_leg = await supervised(lab, "barge")
        await eventually(lambda: sup_leg in bridge_with(lab, customer))
        lab.sim.hangup(sup_leg)
        await eventually(lambda: lab.bus.of_type(m.SUPERVISE_STOPPED))
        assert bridge_with(lab, customer) == {customer, a_leg}
        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_the_call_ending_ends_the_supervision():
    async with brain() as lab:
        customer, a_leg, sup_leg = await supervised(lab, "whisper")
        await eventually(lambda: lab.sim.snoops())
        lab.sim.hangup(a_leg)
        await lab.all_clear()
        assert lab.bus.of_type(m.SUPERVISE_STOPPED)[-1]["reason"] == "the call ended"


@scenario
async def test_only_connected_calls_can_be_supervised():
    async with brain() as lab:
        lab.sim.registered.add(SUP_PHONE)
        await lab.command(
            m.SUPERVISE_START, supervisor=SUP, endpoint=SUP_PHONE, call="no-such-call", mode="monitor"
        )
        await lab.command(m.SUPERVISE_MODE, supervisor=SUP, mode="whisper")
        await eventually(lambda: len(lab.bus.of_type(m.COMMAND_REJECTED)) == 2)
        assert [e["reason"] for e in lab.bus.of_type(m.COMMAND_REJECTED)] == [
            "no connected call with that id",
            "the supervisor supervises nothing",
        ]


@scenario
async def test_a_supervisor_forces_an_agent_state_and_it_is_logged():
    async with brain() as lab:
        await lab.ready(B, PHONE_B)
        await lab.command(m.AGENT_NOT_READY, agent=B, reason="Meeting", by=SUP)
        await eventually(lambda: lab.agent(B).get("availability") == "not_ready")
        forced = lab.bus.of_type(m.AGENT_FORCED)[-1]
        assert (forced["agent"], forced["by"], forced["state"], forced["reason"]) == (
            B,
            SUP,
            "not_ready",
            "Meeting",
        )


@scenario
async def test_live_calls_are_kept_for_the_supervisor_page():
    async with brain() as lab:
        customer, a_leg, sup_leg = await supervised(lab, "monitor")
        call = lab.bus.of_type(m.CALL_CONNECTED)[-1]["call"]
        await eventually(
            lambda: lab.bus.calls.get(call, {}).get("supervisors") == [{"supervisor": SUP, "mode": "monitor"}]
        )
        snapshot = lab.bus.calls[call]
        assert (snapshot["state"], snapshot["agent"], snapshot["queue"]) == ("connected", A, "Support")
        lab.sim.hangup(customer)
        await lab.all_clear()
        await eventually(lambda: call not in lab.bus.calls, what="an ended call leaves the live list")
