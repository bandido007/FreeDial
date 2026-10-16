"""M4b in the brain: calls in from a carrier (a DID), transfers out through trunks, failover only on
carrier failures, and max_channels counted in and out. Real core and service, fake Asterisk."""

from dataclasses import replace

from brain.core.config import EntryConfig, TrunkConfig
from brain.tests.harness import PHONE_A, QUEUE, A, brain, eventually, make_config, scenario
from contracts import messages as m
from contracts.carriers import NumberPlan, OutboundRule, RouteConfig

DID = "+255222123456"
FIRST = TrunkConfig(
    name="first", endpoint="trunk-first", max_channels=2, rule=OutboundRule(strip_digits=4, prefix="0")
)
SECOND = TrunkConfig(name="second", endpoint="trunk-second", rule=OutboundRule())
MOBILES = RouteConfig(
    name="Mobiles", patterns=("+2557",), trunks=("first", "second"), caller_id="+255222123456"
)
TO_FIRST = "PJSIP/0712345678@trunk-first"  # national, as the first carrier wants it
TO_SECOND = "PJSIP/+255712345678@trunk-second"  # E.164, as the second wants it


def carrier_config(**changes):
    config = make_config()
    settings = {
        "entries": {**config.entries, DID: EntryConfig(DID, QUEUE.uid)},
        "trunks": {"first": FIRST, "second": SECOND},
        "routes": (MOBILES,),
        "number_plan": NumberPlan(),
    }
    return replace(config, **{**settings, **changes})


def from_carrier(lab, trunk="trunk-first"):
    """[from-carrier] found the DID and passed it on in E.164 (decision B)."""
    return lab.sim.call_in(DID, caller=trunk, args=["inbound", DID, trunk, ""])


async def on_a_call(lab):
    await lab.ready(A, PHONE_A)
    customer, leg = await lab.connected_call(A)
    return customer, leg


def transfer_out(lab, number="0712345678", kind="blind"):
    return lab.command(m.CALL_TRANSFER, agent=A, kind=kind, target_kind="number", target=number)


@scenario
async def test_a_carrier_call_to_a_did_reaches_an_agent():
    async with brain(config=carrier_config()) as lab:
        await lab.ready(A, PHONE_A)
        from_carrier(lab)
        leg = await lab.sim.next_ringing()
        assert lab.sim.originates()[-1]["endpoint"] == PHONE_A
        lab.sim.answer(leg)
        await eventually(lambda: lab.agent(A).get("activity") == "talking")


@scenario
async def test_a_trunk_at_its_channel_limit_takes_no_more_calls():
    one_line = replace(FIRST, max_channels=1)
    async with brain(config=carrier_config(trunks={"first": one_line, "second": SECOND})) as lab:
        first = from_carrier(lab)
        await eventually(lambda: lab.bus.of_type(m.CALL_STARTED), what="the first call started")
        second = from_carrier(lab)
        await eventually(lambda: second not in lab.sim.channels, what="the second call refused")
        assert first in lab.sim.channels


@scenario
async def test_a_transfer_out_goes_through_the_first_trunk_in_its_format():
    async with brain(config=carrier_config()) as lab:
        customer, _ = await on_a_call(lab)
        lab.sim.registered |= {TO_FIRST, TO_SECOND}
        await transfer_out(lab)
        outside = await lab.sim.next_ringing()
        sent = lab.sim.originates()[-1]
        assert sent["endpoint"] == TO_FIRST
        assert "+255222123456" in sent["callerId"], "the route's caller ID"
        lab.sim.answer(outside)
        await eventually(lambda: lab.sim.bridge_of(outside) == lab.sim.bridge_of(customer))


@scenario
async def test_a_congested_carrier_fails_over_to_the_next_trunk():
    async with brain(config=carrier_config()) as lab:
        customer, _ = await on_a_call(lab)
        lab.sim.registered |= {TO_FIRST, TO_SECOND}
        await transfer_out(lab)
        first = await lab.sim.next_ringing()
        lab.sim.reject(first, cause=34, cause_txt="Circuit/channel congestion")  # SIP 503
        second = await lab.sim.next_ringing()
        assert lab.sim.originates()[-1]["endpoint"] == TO_SECOND
        lab.sim.answer(second)
        await eventually(lambda: lab.sim.bridge_of(second) == lab.sim.bridge_of(customer))
        failed_over = lab.bus.of_type(m.TRUNK_FAILED_OVER)
        assert failed_over and (failed_over[-1]["trunk"], failed_over[-1]["next_trunk"]) == (
            "first",
            "second",
        )


@scenario
async def test_an_unreachable_trunk_fails_over():
    async with brain(config=carrier_config()) as lab:
        await on_a_call(lab)
        lab.sim.registered.add(TO_SECOND)  # the first carrier is not reachable: the originate itself fails
        await transfer_out(lab)
        await lab.sim.next_ringing()
        assert lab.sim.originates()[-1]["endpoint"] == TO_SECOND


@scenario
async def test_busy_never_fails_over():
    async with brain(config=carrier_config()) as lab:
        customer, _ = await on_a_call(lab)
        lab.sim.registered |= {TO_FIRST, TO_SECOND}
        await transfer_out(lab, kind="consult")
        first = await lab.sim.next_ringing()
        lab.sim.reject(first, cause=17)  # the person called is busy: every carrier would say so
        await eventually(lambda: lab.bus.of_type(m.TRANSFER_FAILED), what="the transfer failed")
        assert lab.bus.of_type(m.TRANSFER_FAILED)[-1]["reason"] == "rejected"
        assert TO_SECOND not in [o["endpoint"] for o in lab.sim.originates()]
        assert customer in lab.sim.channels


@scenario
async def test_a_number_no_route_allows_is_refused():
    async with brain(config=carrier_config()) as lab:
        await on_a_call(lab)
        await transfer_out(lab, number="+254712345678")  # Kenya: no route names it
        await eventually(lambda: lab.bus.of_type(m.COMMAND_REJECTED), what="refused")
        assert "no outbound route" in lab.bus.of_type(m.COMMAND_REJECTED)[-1]["reason"]


@scenario
async def test_a_full_first_trunk_is_skipped():
    one_line = replace(FIRST, max_channels=1)
    async with brain(config=carrier_config(trunks={"first": one_line, "second": SECOND})) as lab:
        await lab.ready(A, PHONE_A)
        from_carrier(lab)  # the one line of the first trunk is taken by this call
        leg = await lab.sim.next_ringing()
        lab.sim.answer(leg)
        await eventually(lambda: lab.agent(A).get("activity") == "talking")
        lab.sim.registered |= {TO_FIRST, TO_SECOND}
        await transfer_out(lab)
        await lab.sim.next_ringing()
        assert lab.sim.originates()[-1]["endpoint"] == TO_SECOND


@scenario
async def test_an_internal_number_still_dials_directly():
    async with brain(config=carrier_config()) as lab:
        await on_a_call(lab)
        lab.sim.registered.add("PJSIP/1002")
        await transfer_out(lab, number="1002")
        await lab.sim.next_ringing()
        assert lab.sim.originates()[-1]["endpoint"] == "PJSIP/1002"
