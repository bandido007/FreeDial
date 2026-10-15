"""UX_PRINCIPLES.md section 6: the brain emits the signals its metrics are built from.

offer.answered / offer.missed (seconds to answer, missed offers per 100), wrapup.started /
wrapup.done / wrapup.code_changed (wrap-up seconds, corrections per 100 calls). A wrap-up code can
be corrected for 2 minutes after it was given (section 4: "editable for 2 minutes after submit").
"""

import asyncio
from dataclasses import replace

from brain.tests.harness import PHONE_A, PHONE_B, QUEUE, A, B, brain, eventually, make_config, scenario
from contracts import messages as m


def last(lab, kind):
    return lab.bus.of_type(kind)[-1]


@scenario
async def test_an_answered_offer_says_how_long_it_rang():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        customer, leg = await lab.connected_call(A)
        answered = last(lab, m.OFFER_ANSWERED)
        assert (answered["agent"], answered["leg"]) == (A, leg)
        assert answered["call"] == last(lab, m.CALL_CONNECTED)["call"]
        assert 0 <= answered["seconds"] < 2
        lab.sim.hangup(customer)
        await lab.all_clear()


@scenario
async def test_a_missed_offer_says_why():
    async with brain() as lab:
        await lab.ready(A, PHONE_A)
        await lab.ready(B, PHONE_B)
        lab.sim.call_in("800")
        leg = await lab.sim.next_ringing()
        lab.sim.reject(leg, cause=19, cause_txt="No answer")  # rang out
        await eventually(lambda: lab.bus.of_type(m.OFFER_MISSED))
        missed = last(lab, m.OFFER_MISSED)
        assert (missed["agent"], missed["leg"], missed["reason"]) == (A, leg, "no_answer")
        assert missed["seconds"] >= 0


@scenario
async def test_wrapup_start_done_and_a_code_corrected_in_time():
    config = make_config()
    config = replace(
        config, queues={**config.queues, QUEUE.uid: replace(config.queues[QUEUE.uid], wrapup_seconds=60)}
    )
    async with brain(config=config) as lab:
        await lab.ready(A, PHONE_A)
        customer, _leg = await lab.connected_call(A)
        call = last(lab, m.CALL_CONNECTED)["call"]
        lab.sim.hangup(customer)
        await eventually(lambda: lab.bus.of_type(m.WRAPUP_STARTED))
        started = last(lab, m.WRAPUP_STARTED)
        assert (started["agent"], started["call"], started["limit_seconds"]) == (A, call, 60)

        await lab.command(m.AGENT_WRAPUP_DONE, agent=A, code="Sale")
        await eventually(lambda: lab.bus.of_type(m.WRAPUP_DONE))
        done = last(lab, m.WRAPUP_DONE)
        assert (done["agent"], done["call"], done["code"], done["by"]) == (A, call, "Sale", "agent")
        assert done["seconds"] >= 0

        await lab.command(m.AGENT_WRAPUP_CODE_CHANGE, agent=A, call=call, code="Complaint")
        await eventually(lambda: lab.bus.of_type(m.WRAPUP_CODE_CHANGED))
        changed = last(lab, m.WRAPUP_CODE_CHANGED)
        assert (changed["code"], changed["previous"], changed["call"]) == ("Complaint", "Sale", call)

        await lab.command(m.AGENT_WRAPUP_CODE_CHANGE, agent=A, call=call, code="Complaint")
        await asyncio.sleep(0.2)
        assert len(lab.bus.of_type(m.WRAPUP_CODE_CHANGED)) == 1, "the same code again changes nothing"


@scenario
async def test_a_code_cannot_be_changed_for_another_call_or_after_two_minutes():
    config = make_config()
    config = replace(
        config, queues={**config.queues, QUEUE.uid: replace(config.queues[QUEUE.uid], wrapup_seconds=60)}
    )
    async with brain(config=config) as lab:
        await lab.ready(A, PHONE_A)
        customer, _leg = await lab.connected_call(A)
        call = last(lab, m.CALL_CONNECTED)["call"]
        lab.sim.hangup(customer)
        await eventually(lambda: lab.bus.of_type(m.WRAPUP_STARTED))
        await lab.command(m.AGENT_WRAPUP_DONE, agent=A, code="Sale")
        await eventually(lambda: lab.bus.of_type(m.WRAPUP_DONE))

        wrong = await lab.command(m.AGENT_WRAPUP_CODE_CHANGE, agent=A, call="another-call", code="Complaint")
        await eventually(
            lambda: any(e["command_id"] == wrong.command_id for e in lab.bus.of_type(m.COMMAND_REJECTED))
        )
        lab.runtime.core.agents[A].last_wrap = (call, "Sale", lab.runtime.core.agents[A].last_wrap[2] - 121)
        late = await lab.command(m.AGENT_WRAPUP_CODE_CHANGE, agent=A, call=call, code="Complaint")
        await eventually(
            lambda: any(e["command_id"] == late.command_id for e in lab.bus.of_type(m.COMMAND_REJECTED))
        )
        assert not lab.bus.of_type(m.WRAPUP_CODE_CHANGED)
