"""M5: transfers and conference, through the real brain service and the fake Asterisk.

The agent on the call (A) transfers: blind to an agent, a queue or a number; consult with
complete or cancel; a 3-way conference with drop. Every leg is originated with the
customer's channel as originator, so the whole call keeps one linkedid (rule 4).
"""

from brain.sim import FakeAri
from brain.tests.harness import PHONE_A, PHONE_B, PHONE_C, A, B, C, brain, eventually, scenario
from contracts import messages as m


async def on_a_call(lab):
    """A, B and C are Ready on their phones; a customer is talking with A. Returns (customer, A's leg)."""
    await lab.ready(A, PHONE_A)
    await lab.ready(B, PHONE_B)
    await lab.ready(C, PHONE_C)
    customer = lab.sim.call_in("800")
    leg = await lab.sim.next_ringing()
    assert lab.sim.channels[leg]["name"].startswith(PHONE_A), "A has been idle longest"
    lab.sim.answer(leg)
    await eventually(lambda: lab.agent(A).get("activity") == "talking")
    await eventually(lambda: lab.sim.bridge_of(leg) is not None)
    return customer, leg


def main_bridge(lab, customer):
    return lab.sim.bridge_of(customer)


def in_bridge(lab, customer):
    bridge = main_bridge(lab, customer)
    return set(bridge["channels"]) if bridge else set()


def every_originate_shares_the_customers_linkedid(sim: FakeAri, customer):
    assert all(params["originator"] == customer for params in sim.originates()), sim.originates()


def last(lab, kind):
    return lab.bus.of_type(kind)[-1]


# ── blind ─────────────────────────────────────────────────────────────────
@scenario
async def test_blind_transfer_to_an_agent():
    async with brain() as lab:
        customer, a_leg = await on_a_call(lab)
        await lab.command(m.CALL_TRANSFER, agent=A, kind="blind", target_kind="agent", target=B)
        b_leg = await lab.sim.next_ringing()
        assert lab.sim.channels[b_leg]["name"].startswith(PHONE_B)
        await eventually(lambda: a_leg not in lab.sim.channels, what="A left at once")
        assert main_bridge(lab, customer)["id"] in lab.sim.moh, "the customer hears music meanwhile"
        assert lab.agent(A)["activity"] == "idle" and lab.agent(B)["activity"] == "offered"

        lab.sim.answer(b_leg)
        await eventually(lambda: in_bridge(lab, customer) == {customer, b_leg})
        assert main_bridge(lab, customer)["id"] not in lab.sim.moh
        assert lab.agent(B)["activity"] == "talking"
        transfer = last(lab, m.TRANSFER_COMPLETED)["transfer"]
        assert last(lab, m.TRANSFER_STARTED)["transfer"] == transfer

        lab.sim.hangup(customer)
        await lab.all_clear()
        every_originate_shares_the_customers_linkedid(lab.sim, customer)


@scenario
async def test_a_blind_transfer_nobody_answers_goes_back_to_the_queue():
    async with brain(config=None) as lab:
        customer, _ = await on_a_call(lab)
        await lab.command(m.CALL_TRANSFER, agent=A, kind="blind", target_kind="agent", target=C)
        c_leg = await lab.sim.next_ringing()
        lab.sim.reject(c_leg)
        await eventually(lambda: lab.bus.of_type(m.TRANSFER_FAILED))
        assert last(lab, m.TRANSFER_FAILED)["reason"] == "rejected"
        back = await lab.sim.next_ringing()  # the call is in Support again: the customer is not lost
        assert lab.sim.channels[back]["name"].startswith((PHONE_A, PHONE_B))
        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_blind_transfer_to_a_queue_with_a_required_skill():
    async with brain() as lab:
        customer, a_leg = await on_a_call(lab)
        await lab.command(
            m.CALL_TRANSFER,
            agent=A,
            kind="blind",
            target_kind="queue",
            target="Backup",
            skills={"billing": 5},
        )
        c_leg = await lab.sim.next_ringing()
        assert lab.sim.channels[c_leg]["name"].startswith(PHONE_C)
        ticket = last(lab, m.ROUTING_TICKET)
        assert (ticket["queue"], ticket["skills"], ticket["source"]) == ("Backup", {"billing": 5}, "transfer")
        lab.sim.answer(c_leg)
        await eventually(lambda: in_bridge(lab, customer) == {customer, c_leg})
        assert a_leg not in lab.sim.channels
        lab.sim.hangup(c_leg)
        await lab.all_clear()


@scenario
async def test_blind_transfer_to_a_number():
    async with brain() as lab:
        customer, _ = await on_a_call(lab)
        lab.sim.registered.add("PJSIP/1002")
        await lab.command(m.CALL_TRANSFER, agent=A, kind="blind", target_kind="number", target="1002")
        outside = await lab.sim.next_ringing()
        assert lab.sim.originates()[-1]["endpoint"] == "PJSIP/1002"
        lab.sim.answer(outside)
        await eventually(lambda: in_bridge(lab, customer) == {customer, outside})
        lab.sim.hangup(outside)  # the person called hangs up: the call is over
        await lab.all_clear()
        assert lab.ended()[-1]["result"] == "answered"


# ── consult ───────────────────────────────────────────────────────────────
async def consulting(lab, customer, a_leg):
    await lab.command(m.CALL_TRANSFER, agent=A, kind="consult", target_kind="agent", target=B)
    b_leg = await lab.sim.next_ringing()
    await eventually(lambda: in_bridge(lab, customer) == {customer}, what="A out of the customer's bridge")
    assert main_bridge(lab, customer)["id"] in lab.sim.moh, "the customer is on hold"
    lab.sim.answer(b_leg)
    await eventually(
        lambda: lab.sim.bridge_of(a_leg) is not None and lab.sim.bridge_of(a_leg) == lab.sim.bridge_of(b_leg)
    )
    assert lab.sim.bridge_of(a_leg)["id"] != main_bridge(lab, customer)["id"], "A and B talk privately"
    return b_leg


@scenario
async def test_consult_then_complete():
    async with brain() as lab:
        customer, a_leg = await on_a_call(lab)
        b_leg = await consulting(lab, customer, a_leg)
        consult_bridge = lab.sim.bridge_of(a_leg)["id"]
        await lab.command(m.TRANSFER_COMPLETE, agent=A)
        await eventually(lambda: in_bridge(lab, customer) == {customer, b_leg})
        assert a_leg not in lab.sim.channels and consult_bridge not in lab.sim.bridges
        assert main_bridge(lab, customer)["id"] not in lab.sim.moh
        assert lab.agent(B)["activity"] == "talking" and lab.agent(A)["activity"] == "idle"
        lab.sim.hangup(customer)
        await lab.all_clear()
        every_originate_shares_the_customers_linkedid(lab.sim, customer)


@scenario
async def test_every_transfer_event_names_both_agents_so_their_pages_get_it():
    """The live relay sends an event to the pages of the agents it names (agent, target_agent).
    A page that missed transfer.answered never enabled Complete / Conference (found in Chrome)."""
    async with brain() as lab:
        customer, a_leg = await on_a_call(lab)
        await consulting(lab, customer, a_leg)
        await lab.command(m.CONFERENCE_START, agent=A)
        await eventually(lambda: lab.bus.of_type(m.CONFERENCE_STARTED))
        for kind in (m.TRANSFER_STARTED, m.TRANSFER_ANSWERED, m.CONFERENCE_STARTED):
            data = last(lab, kind)
            assert (data["agent"], data["target_agent"]) == (A, B), (kind, data)
        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_consult_then_cancel_puts_the_agent_back_with_the_customer():
    async with brain() as lab:
        customer, a_leg = await on_a_call(lab)
        b_leg = await consulting(lab, customer, a_leg)
        await lab.command(m.TRANSFER_CANCEL, agent=A)
        await eventually(lambda: in_bridge(lab, customer) == {customer, a_leg})
        assert b_leg not in lab.sim.channels and main_bridge(lab, customer)["id"] not in lab.sim.moh
        assert last(lab, m.TRANSFER_CANCELLED)["reason"] == "cancelled by the agent"
        assert lab.agent(A)["activity"] == "talking" and lab.agent(B)["activity"] == "idle"
        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_the_consulted_agent_hanging_up_cancels_it():
    async with brain() as lab:
        customer, a_leg = await on_a_call(lab)
        b_leg = await consulting(lab, customer, a_leg)
        lab.sim.hangup(b_leg)
        await eventually(lambda: in_bridge(lab, customer) == {customer, a_leg})
        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_the_agent_hanging_up_while_consulting_completes_it():
    async with brain() as lab:
        customer, a_leg = await on_a_call(lab)
        b_leg = await consulting(lab, customer, a_leg)
        lab.sim.hangup(a_leg)
        await eventually(lambda: in_bridge(lab, customer) == {customer, b_leg})
        assert last(lab, m.TRANSFER_COMPLETED)
        lab.sim.hangup(b_leg)
        await lab.all_clear()


@scenario
async def test_the_customer_hanging_up_during_a_consult_ends_everything():
    async with brain() as lab:
        customer, a_leg = await on_a_call(lab)
        await consulting(lab, customer, a_leg)
        lab.sim.hangup(customer)
        await lab.all_clear()
        assert {lab.agent(A)["activity"], lab.agent(B)["activity"]} == {"idle"}


# ── conference ────────────────────────────────────────────────────────────
@scenario
async def test_three_way_conference_then_drop_the_other_party():
    async with brain() as lab:
        customer, a_leg = await on_a_call(lab)
        b_leg = await consulting(lab, customer, a_leg)
        await lab.command(m.CONFERENCE_START, agent=A)
        await eventually(lambda: in_bridge(lab, customer) == {customer, a_leg, b_leg})
        assert main_bridge(lab, customer)["id"] not in lab.sim.moh and len(lab.sim.bridges) == 1
        await lab.command(m.CONFERENCE_DROP, agent=A, who="other")
        await eventually(lambda: in_bridge(lab, customer) == {customer, a_leg})
        assert b_leg not in lab.sim.channels and lab.agent(B)["activity"] == "idle"
        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_leaving_a_conference_leaves_the_customer_with_the_other_party():
    async with brain() as lab:
        customer, a_leg = await on_a_call(lab)
        b_leg = await consulting(lab, customer, a_leg)
        await lab.command(m.CONFERENCE_START, agent=A)
        await eventually(lambda: in_bridge(lab, customer) == {customer, a_leg, b_leg})
        await lab.command(m.CONFERENCE_DROP, agent=A, who="me")
        await eventually(lambda: in_bridge(lab, customer) == {customer, b_leg})
        assert lab.agent(B)["activity"] == "talking"
        lab.sim.hangup(b_leg)
        await lab.all_clear()


# ── what is refused ───────────────────────────────────────────────────────
@scenario
async def test_transfers_that_cannot_work_are_refused_with_a_reason():
    async with brain() as lab:
        customer, _ = await on_a_call(lab)
        await lab.command(
            m.CALL_TRANSFER, agent=C, kind="blind", target_kind="agent", target=B
        )  # C is on no call
        await eventually(lambda: lab.bus.of_type(m.COMMAND_REJECTED))
        await lab.command(m.AGENT_LOGOUT, agent=C)
        await eventually(lambda: lab.agent(C).get("availability") == "logged_out")
        await lab.command(m.CALL_TRANSFER, agent=A, kind="blind", target_kind="agent", target=C)
        await lab.command(m.CALL_TRANSFER, agent=A, kind="blind", target_kind="ai", target="Msaidizi")
        await lab.command(m.CALL_TRANSFER, agent=A, kind="consult", target_kind="queue", target="Backup")
        await lab.command(m.CALL_TRANSFER, agent=A, kind="blind", target_kind="queue", target="Nowhere")
        await lab.command(m.CALL_TRANSFER, agent=A, kind="blind", target_kind="number", target="10 02;x")
        await eventually(lambda: len(lab.bus.of_type(m.COMMAND_REJECTED)) == 6)
        reasons = [event["reason"] for event in lab.bus.of_type(m.COMMAND_REJECTED)]
        assert reasons == [
            "the agent has no connected call",
            "the target agent is not free",
            "transfers to an AI agent come with M8",
            "a consult goes to an agent or a number",
            "unknown queue",
            "not a dialable number",
        ]
        assert in_bridge(lab, customer) and lab.agent(A)["activity"] == "talking", (
            "the call goes on untouched"
        )
        lab.sim.hangup(customer)
        await lab.all_clear()
