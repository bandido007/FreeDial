"""M9: every time a call starts waiting in a queue the brain says so (call.queued), once, with why.
Reports measure each queue's offered calls and waits from these events."""

from brain.core.config import OverflowAction
from brain.tests.harness import BACKUP, PHONE_A, PHONE_C, A, C, brain, eventually, make_config, scenario
from brain.tests.test_ai import ai_config, talking_to_the_ai
from contracts import messages as m


def queued(lab, call=None):
    return [
        (event["queue"], event["reason"])
        for event in lab.bus.of_type(m.CALL_QUEUED)
        if call is None or event["call"] == call
    ]


@scenario
async def test_a_call_to_an_entry_point_is_queued_once():
    async with brain() as lab:
        customer = lab.sim.call_in("800")
        await eventually(lambda: queued(lab))
        assert queued(lab) == [("Support", "entry")]
        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_an_overflow_queues_the_call_again_elsewhere():
    config = make_config(max_wait=0.2, overflow_action=OverflowAction.QUEUE, overflow_queue=BACKUP.uid)
    async with brain(config=config) as lab:
        customer = lab.sim.call_in("800")
        await eventually(lambda: len(queued(lab)) == 2)
        assert queued(lab) == [("Support", "entry"), ("Backup", "overflow")]
        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_the_ai_queues_its_caller_once_where_it_really_waits():
    async with brain(config=ai_config()) as lab:
        await lab.ready(C, PHONE_C)
        _customer, call, _leg = await talking_to_the_ai(lab)
        assert queued(lab) == [], "talking to the AI is not waiting in a queue"
        await lab.command(m.AI_HANDOFF, call=call, reason="ai_decided", summary="s", queue="Backup")
        await lab.sim.next_ringing()
        assert queued(lab, call) == [("Backup", "ai_handoff")]


@scenario
async def test_an_ai_handoff_without_a_queue_waits_in_the_entrys_queue():
    async with brain(config=ai_config()) as lab:
        await lab.ready(A, PHONE_A)
        _customer, call, _leg = await talking_to_the_ai(lab)
        await lab.command(m.AI_HANDOFF, call=call, reason="asked_for_person", summary="s")
        await lab.sim.next_ringing()
        assert queued(lab, call) == [("Support", "ai_handoff")]


@scenario
async def test_a_rejected_ai_choice_waits_in_the_fallback():
    async with brain(config=ai_config()) as lab:
        await lab.ready(C, PHONE_C)
        _customer, call, _leg = await talking_to_the_ai(lab)
        await lab.command(m.AI_HANDOFF, call=call, reason="ai_decided", summary="s", queue="Fraud")
        await lab.sim.next_ringing()
        assert queued(lab, call) == [("Backup", "ai_handoff")]


@scenario
async def test_a_failed_ai_queues_its_caller_in_the_fallback():
    async with brain(config=ai_config()) as lab:
        await lab.ready(C, PHONE_C)
        _customer, call, leg = await talking_to_the_ai(lab)
        lab.sim.hangup(leg)
        await lab.sim.next_ringing()
        assert queued(lab, call) == [("Backup", "ai_failed")]


@scenario
async def test_a_blind_transfer_to_a_queue_queues_the_call_there():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        await lab.ready(C, PHONE_C)
        customer, _leg = await lab.connected_call(A)
        await lab.command(m.CALL_TRANSFER, agent=A, kind="blind", target_kind="queue", target="Backup")
        await lab.sim.next_ringing()
        call = lab.bus.of_type(m.CALL_STARTED)[-1]["call"]
        assert queued(lab, call) == [("Support", "entry"), ("Backup", "transfer")]
        lab.sim.hangup(customer)
        await lab.all_clear()
