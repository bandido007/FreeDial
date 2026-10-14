"""M8: an AI agent answers an entry point, then hands the call to people (or resolves it).

The brain opens the AI's media leg with ARI externalMedia (WebSocket, Asterisk as the server):
the AI worker connects to it, and the leg enters the app with args ai,<call>,<ai agent>. The
worker ends its part with a command: ai.handoff (the call goes to a queue, the summary travels
with the offer) or ai.hangup (resolved). If the AI never comes up or its leg dies, the call goes
to the entry point's fallback queue: a caller is never left talking to nobody.
"""

from dataclasses import replace

from brain.core.config import AiAgentConfig, EntryConfig
from brain.sim import FakeAri, FakeBus
from brain.tests.harness import (
    BACKUP,
    PHONE_A,
    PHONE_C,
    QUEUE,
    A,
    C,
    brain,
    eventually,
    make_config,
    scenario,
)
from contracts import messages as m

AI = "ai-uid-1"


def ai_config(start_timeout: float = 10.0):
    config = make_config()
    entries = {
        **config.entries,
        "802": EntryConfig("802", QUEUE.uid, language="english", fallback_queue=BACKUP.uid, ai_agent=AI),
    }
    skills = {**config.skills, "english": "language", "swahili": "language"}
    return replace(
        config,
        entries=entries,
        skills=skills,
        ai_agents={AI: AiAgentConfig(AI, "Jambo AI", start_timeout=start_timeout)},
    )


def last(lab, kind):
    return lab.bus.of_type(kind)[-1]


async def talking_to_the_ai(lab, *, connect: bool = True):
    """A customer calls 802; the AI's leg comes up. Returns (customer, call id, AI leg)."""
    customer = lab.sim.call_in("802")
    await eventually(lambda: lab.bus.of_type(m.AI_STARTED), what="ai.started")
    started = last(lab, m.AI_STARTED)
    leg = started["leg"]
    if connect:
        lab.sim.ai_connect(leg)
        await eventually(lambda: lab.sim.bridge_of(leg) is not None, what="AI leg in the bridge")
    return customer, started["call"], leg


# ── the AI answers ────────────────────────────────────────────────────────
@scenario
async def test_the_ai_answers_the_entry_and_shares_the_callers_bridge():
    async with brain(config=ai_config()) as lab:
        await lab.ready(A, PHONE_A)
        customer, call, leg = await talking_to_the_ai(lab)
        assert lab.sim.channels[customer]["state"] == "Up"
        assert set(lab.sim.bridge_of(customer)["channels"]) == {customer, leg}
        assert lab.sim.bridge_of(customer)["id"] not in lab.sim.moh, "no hold music while the AI talks"
        assert lab.sim.ringing.empty() and lab.agent(A)["activity"] == "idle", "no agent rung yet"

        media = lab.sim.external_media()[-1]
        assert media["transport"] == "websocket" and media["connection_type"] == "server"
        assert media["encapsulation"] == "none" and media["format"] == "slin24"
        assert media["data"] == f"ai,{call},{AI}" and media["channelId"] == leg
        started = last(lab, m.AI_STARTED)
        assert (started["ai_agent"], started["language"], started["entry"]) == (AI, "english", "802")
        assert last(lab, m.AI_CONNECTED) == {"call": call, "leg": leg}
        assert lab.bus.calls[call]["state"] == "ai", "supervisors see the AI part live"

        lab.sim.hangup(customer)
        await lab.all_clear()
        ended = last(lab, m.AI_ENDED)
        assert (ended["outcome"], ended["reason"]) == ("caller_left", "the caller hung up")
        assert last(lab, m.CALL_ENDED)["result"] == "ai_handled"


# ── handoff to people ─────────────────────────────────────────────────────
@scenario
async def test_handoff_queues_the_call_and_the_agent_gets_the_summary():
    async with brain(config=ai_config()) as lab:
        await lab.ready(A, PHONE_A)
        customer, call, leg = await talking_to_the_ai(lab)
        transcript = [{"who": "caller", "text": "I want a person", "at": 3.0}]
        await lab.command(
            m.AI_HANDOFF,
            call=call,
            reason="asked_for_person",
            summary="Member asks about a claim payment; wants a person.",
            transcript=transcript,
        )
        await eventually(lambda: leg not in lab.sim.channels, what="the AI's leg hung up")
        ended = last(lab, m.AI_ENDED)
        assert ended["outcome"] == "handoff" and ended["reason"] == "asked_for_person"
        assert ended["summary"].startswith("Member asks") and ended["transcript"] == transcript
        assert lab.sim.bridge_of(customer)["id"] in lab.sim.moh, "hold music while waiting for a person"

        agent_leg = await lab.sim.next_ringing()
        assert lab.sim.channels[agent_leg]["name"].startswith(PHONE_A)
        offered = last(lab, m.CALL_OFFERED)
        assert offered["summary"] == "Member asks about a claim payment; wants a person."
        assert offered["ai_reason"] == "asked_for_person"
        lab.sim.answer(agent_leg)
        await eventually(lambda: lab.agent(A).get("activity") == "talking")
        assert set(lab.sim.bridge_of(customer)["channels"]) == {customer, agent_leg}
        assert lab.bus.calls[call]["summary"].startswith("Member asks")
        lab.sim.hangup(customer)
        await lab.all_clear()
        assert last(lab, m.CALL_ENDED)["result"] == "answered"


@scenario
async def test_the_ai_picks_a_configured_queue_and_skills():
    async with brain(config=ai_config()) as lab:
        await lab.ready(C, PHONE_C)
        _customer, call, _leg = await talking_to_the_ai(lab)
        await lab.command(
            m.AI_HANDOFF,
            call=call,
            reason="ai_decided",
            summary="Billing question.",
            queue="Backup",
            skills={"billing": 5},
        )
        agent_leg = await lab.sim.next_ringing()
        assert lab.sim.channels[agent_leg]["name"].startswith(PHONE_C)
        ticket = last(lab, m.ROUTING_TICKET)
        assert (ticket["queue"], ticket["skills"], ticket["source"]) == ("Backup", {"billing": 5}, "ai")
        assert ticket["language"] == "english"


@scenario
async def test_what_is_not_configured_is_rejected_and_the_call_goes_to_the_fallback():
    async with brain(config=ai_config()) as lab:
        await lab.ready(C, PHONE_C)
        _customer, call, _leg = await talking_to_the_ai(lab)
        await lab.command(
            m.AI_HANDOFF, call=call, reason="ai_decided", summary="Fraud report.", skills={"fraud": 7}
        )
        agent_leg = await lab.sim.next_ringing()
        assert lab.sim.channels[agent_leg]["name"].startswith(PHONE_C), "fallback queue Backup"
        rejected = last(lab, m.ROUTING_AI_TAG_REJECTED)
        assert rejected["source"] == "ai" and rejected["rejected"] == {"skills": ["fraud"]}


@scenario
async def test_a_second_handoff_changes_nothing():
    async with brain(config=ai_config()) as lab:
        await lab.ready(A, PHONE_A)
        _customer, call, _leg = await talking_to_the_ai(lab)
        await lab.command(m.AI_HANDOFF, call=call, reason="asked_for_person", summary="s")
        await lab.sim.next_ringing()
        again = await lab.command(m.AI_HANDOFF, call=call, reason="asked_for_person", summary="s")
        await eventually(lambda: lab.bus.of_type(m.COMMAND_REJECTED), what="refused")
        assert last(lab, m.COMMAND_REJECTED)["command_id"] == again.command_id
        assert len(lab.bus.of_type(m.AI_ENDED)) == 1 and lab.sim.ringing.empty()


# ── resolved by the AI ────────────────────────────────────────────────────
@scenario
async def test_the_ai_resolves_the_call():
    async with brain(config=ai_config()) as lab:
        _customer, call, _leg = await talking_to_the_ai(lab)
        await lab.command(m.AI_HANGUP, call=call, reason="answered the question", summary="Opening hours.")
        await lab.all_clear()
        ended = last(lab, m.AI_ENDED)
        assert (ended["outcome"], ended["summary"]) == ("resolved", "Opening hours.")
        call_ended = last(lab, m.CALL_ENDED)
        assert (call_ended["result"], call_ended["reason"]) == ("ai_handled", "answered the question")


# ── when the AI fails, people take over ───────────────────────────────────
@scenario
async def test_the_ai_leg_dies_and_the_call_goes_to_the_fallback_queue():
    async with brain(config=ai_config()) as lab:
        await lab.ready(C, PHONE_C)
        customer, _call, leg = await talking_to_the_ai(lab)
        lab.sim.hangup(leg)  # the worker crashed: Asterisk drops the WebSocket channel
        agent_leg = await lab.sim.next_ringing()
        assert lab.sim.channels[agent_leg]["name"].startswith(PHONE_C)
        assert last(lab, m.AI_ENDED)["outcome"] == "failed"
        assert customer in lab.sim.channels and lab.sim.bridge_of(customer)["id"] in lab.sim.moh


@scenario
async def test_no_ai_worker_connects_in_time():
    async with brain(config=ai_config(start_timeout=0.2)) as lab:
        await lab.ready(C, PHONE_C)
        customer, _call, leg = await talking_to_the_ai(lab, connect=False)
        agent_leg = await lab.sim.next_ringing()
        assert leg not in lab.sim.channels, "the unanswered AI leg is hung up"
        assert lab.sim.channels[agent_leg]["name"].startswith(PHONE_C)
        ended = last(lab, m.AI_ENDED)
        assert (ended["outcome"], ended["reason"]) == ("failed", "the AI did not come up")
        assert customer in lab.sim.channels


@scenario
async def test_an_entry_whose_ai_agent_is_gone_goes_straight_to_its_queue():
    config = ai_config()
    async with brain(config=replace(config, ai_agents={})) as lab:
        await lab.ready(A, PHONE_A)
        lab.sim.call_in("802")
        agent_leg = await lab.sim.next_ringing()
        assert lab.sim.channels[agent_leg]["name"].startswith(PHONE_A)
        assert not lab.bus.of_type(m.AI_STARTED)


@scenario
async def test_a_restarted_brain_hangs_up_the_ai_and_queues_the_caller():
    """Rule 1: the new brain finds the caller with an AI leg it knows nothing about. The worker's
    conversation is lost with the old brain's call, so the caller waits for a person instead."""
    sim, bus, config = FakeAri(), FakeBus(), ai_config()
    async with brain(sim=sim, bus=bus, config=config) as lab:
        await lab.ready(A, PHONE_A)
        customer, call, leg = await talking_to_the_ai(lab)
    async with brain(sim=sim, bus=bus, config=config) as lab:
        agent_leg = await lab.sim.next_ringing()
        assert leg not in lab.sim.channels, "the AI's leg is hung up"
        assert customer in lab.sim.channels and lab.sim.channels[agent_leg]["name"].startswith(PHONE_A)
        assert call in last(lab, m.BRAIN_RECONCILED)["adopted"]
        lab.sim.answer(agent_leg)
        await eventually(lambda: set(lab.sim.bridge_of(customer)["channels"]) == {customer, agent_leg})
