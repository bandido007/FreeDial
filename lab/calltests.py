"""The brain's 9 call tests (docs/POC_PLAN.md), plus 10 ring all, 11 voicemail and 12-17 transfers (M5),
and 18 supervision (M6) on the real Asterisk. The transfer tests also check CEL: one linkedid for the
whole call; test 18 checks, by the tones each phone receives, that only the right people hear the supervisor.

    cd ~/AlgoDial && .venv/bin/python lab/calltests.py            # all
    .venv/bin/python lab/calltests.py 1 4 8                       # some

What it does:
  * makes sure the lab setup exists: SIP accounts lab-customer, lab-agent-a, lab-agent-b;
    agents 9001 and 9002; queue "Support"; entry point 899 -> Support (800-802 are the demo centre)
  * starts the brain itself (stop yours first: two brains on one app would fight),
    recording every decision to brain/sim/recordings/lab.jsonl (a full run starts it afresh;
    a run of some tests records to lab/calltests-partial.jsonl instead)
  * plays the customer and both agents with lab/sipphone.py
  * after each test checks, through ARI, that no channel and no bridge is left
    (what `core show channels` and `bridge show all` would say)

Test 9 needs the dialplan fallback: sudo deploy/asterisk/apply.sh m2
Your Zoipers (1001, 1002) are in Queue(support): during test 9 they may ring for a second.
"""
import asyncio
import csv
import io
import json
import os
import signal
import struct
import subprocess
import sys
import time
import traceback
import wave
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lab"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

from apps.agents.models import Agent  # noqa: E402
from apps.calls import asterisk, bus, recordings  # noqa: E402
from apps.core.crypto import decrypt_secret  # noqa: E402
from apps.queues import services as queues  # noqa: E402
from apps.queues.models import EntryPoint, Queue  # noqa: E402
from apps.telephony import services as telephony  # noqa: E402
from apps.telephony.models import SipAccount  # noqa: E402
from contracts import messages as m  # noqa: E402
from contracts.envelope import decode  # noqa: E402
from sipphone import Phone, tone_share  # noqa: E402

CEL = "/var/log/asterisk/cel-custom/cel_trace.csv"
LAB_ENTRY = "899"  # needs `sudo deploy/asterisk/apply.sh m4` (every 8xx number goes to the brain)
ALL_TESTS = list(range(1, 21))
LAB_AI_ENTRY = "897"  # the lab AI agent answers, then hands off to queue Support (test 20)
AI_LOG = ROOT / "lab" / "calltests-ai.log"
VOICES = ROOT / "lab" / "voices"  # the scripted customer's recorded sentences (not in git)
RECORDING = ROOT / "brain" / "sim" / "recordings" / "lab.jsonl"  # a full run; test_replay.py replays it
PARTIAL_RECORDING = ROOT / "lab" / "calltests-partial.jsonl"  # a run of some tests (git-ignored)
BRAIN_LOG = ROOT / "lab" / "calltests-brain.log"


def brain_running():
    """Another brain on this machine? (ARI's app list is not proof: Asterisk keeps an app listed while
    a bridge left behind is still subscribed to it, even with nobody connected.)"""
    return subprocess.run(["pgrep", "-f", "manage.py run_brain"], capture_output=True).returncode == 0


def say(text):
    print(f"{time.strftime('%H:%M:%S')}  {text}", flush=True)


# ── lab setup (idempotent) ─────────────────────────────────────────────────
def ensure_setup():
    passwords = {}
    for sip_id in ("lab-customer", "lab-agent-a", "lab-agent-b"):
        account = SipAccount.objects.filter(sip_id=sip_id).first() or telephony.create_sip_account(
            actor=None, sip_id=sip_id, kind="SOFTPHONE", display_name=sip_id
        )
        passwords[sip_id] = decrypt_secret(account.password_encrypted)
    agents = {}
    for number, name in (("9001", "Lab agent A"), ("9002", "Lab agent B")):
        agent = Agent.objects.filter(number=number).first() or Agent.objects.create(number=number, name=name)
        agents[number] = str(agent.uid)
    support = Queue.objects.filter(name__iexact="Support").first()
    if support is None:
        support = queues.save_queue(actor=None, name="Support", ring_timeout=8, max_wait=120, retry_delay=3)
    have = set(support.agents.values_list("number", flat=True))
    wanted = list(support.agents.all()) + [Agent.objects.get(number=n) for n in agents if n not in have]
    queues.save_queue(actor=None, uid=support.uid, name=support.name, agents=wanted)
    entry = EntryPoint.objects.filter(number=LAB_ENTRY).first()
    if entry is None:
        queues.save_entry_point(actor=None, number=LAB_ENTRY, queue=support, name="Lab tests")
    elif entry.queue_id != support.pk:
        entry.queue = support
        entry.save()
    ensure_lab_ai(support)
    support.refresh_from_db()
    return passwords, agents["9001"], agents["9002"], support.ring_timeout


def ensure_lab_ai(support):
    """The lab AI agent (the demo helpline's knowledge) on entry 897, handing callers to Support."""
    from apps.agents.models import Skill
    from apps.ai import demo
    from apps.ai.models import AiAgent, AiRoute

    ai = AiAgent.objects.filter(name="Lab AI", deleted=False).first() or AiAgent(name="Lab AI")
    ai.instructions = demo.INSTRUCTIONS.replace("Complaints go to Escalations. Anything else goes to General.", "")
    ai.greeting_english = "Hello, this is the lab assistant. How can I help you?"
    ai.greeting_swahili = "Habari, mimi ni msaidizi wa maabara. Nikusaidie nini?"
    ai.active = True
    ai.save()
    AiRoute.objects.update_or_create(ai_agent=ai, queue=support, defaults={"when": "anything a person must handle"})
    AiRoute.objects.filter(ai_agent=ai).exclude(queue=support).delete()
    entry = EntryPoint.objects.filter(number=LAB_AI_ENTRY).first()
    if entry is None:
        queues.save_entry_point(actor=None, number=LAB_AI_ENTRY, queue=support, name="Lab AI tests")
        entry = EntryPoint.objects.get(number=LAB_AI_ENTRY)
    entry.queue, entry.ai_agent = support, ai
    entry.language = Skill.objects.filter(name="english", deleted=False).first()
    entry.save()


async def voice(text):
    """``text`` as a caller says it on a phone line (8 kHz 16-bit), made once with OpenAI and kept."""
    import hashlib

    from aiworker.adapters.openai import OpenAi
    from aiworker.core.audio import telephone_line

    path = VOICES / f"{hashlib.sha1(text.encode()).hexdigest()[:16]}.pcm"
    if path.exists():
        return path.read_bytes()
    assert settings.OPENAI_API_KEY, "OPENAI_API_KEY is not set (.env)"
    style = "You are a caller on the phone. Speak English with a Tanzanian accent, at a natural pace."
    async with aiohttp.ClientSession() as http:
        speech = OpenAi(http, settings.OPENAI_API_KEY)
        chunks = [c async for c in speech.speak(text, model="gpt-4o-mini-tts", voice="ash", style=style, timeout=90)]
    line = telephone_line(b"".join(chunks))
    VOICES.mkdir(parents=True, exist_ok=True)
    path.write_bytes(line)
    return line


def set_strategy(strategy):
    """Switch queue Support's strategy the normal way (the brain hears config.changed); returns the old one."""
    return set_support(strategy=strategy)["strategy"]


def set_support(**fields):
    """Change queue Support's settings the normal way; returns the old values."""
    support = Queue.objects.get(name__iexact="Support")
    before = {field: getattr(support, field) for field in fields}
    queues.save_queue(actor=None, uid=support.uid, name=support.name, **fields)
    return before


# ── the world as Asterisk and the bus see it ───────────────────────────────
class World:
    def __init__(self, http, recording):
        self.http = http
        self.recording = recording
        self.redis = bus.client()
        self.cursor = "$"
        self.seen = []
        self.brain = None
        self.dialled = []  # linkedids of this run's calls: their recordings are removed at the end

    async def ari(self, method, path, **params):
        async with self.http.request(method, settings.ARI_URL + path, params=params) as response:
            if response.status >= 300:
                raise RuntimeError(f"ARI {method} {path} -> {response.status} {await response.text()}")
            return None if response.status == 204 else await response.json()

    async def brain_connected(self):
        apps = await self.ari("GET", "/applications")
        return any(app["name"] == settings.ARI_APP for app in apps)

    def mark(self):
        last = self.redis.xrevrange(settings.BUS_EVENTS_STREAM, count=1)
        self.cursor = last[0][0].decode() if last else "0"

    def new_events(self):
        out = []
        for entry_id, fields in self.redis.xrange(settings.BUS_EVENTS_STREAM, min=f"({self.cursor}" if self.cursor != "0" else "-"):
            out.append(decode(fields[b"message"].decode()))
        return out

    async def event(self, kind, timeout=15, **match):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.new_events():
                if event.type == kind and all(event.data.get(k) == v for k, v in match.items()):
                    return event.data
            await asyncio.sleep(0.1)
        raise AssertionError(f"no {kind} {match or ''} within {timeout}s")

    def command(self, command_type, /, **data):
        return bus.send_command(command_type, data)

    def agent_state(self, uid):
        raw = self.redis.hget(settings.BUS_AGENTS_KEY, uid)
        return json.loads(raw) if raw else {}

    async def agent_is(self, uid, timeout=10, **want):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.agent_state(uid)
            if all(state.get(k) == v for k, v in want.items()):
                return state
            await asyncio.sleep(0.1)
        raise AssertionError(f"agent {uid} never {want}: {self.agent_state(uid)}")

    async def all_clear(self, timeout=6):
        """No channel and no bridge left in Asterisk."""
        deadline = time.monotonic() + timeout
        while True:
            channels = await self.ari("GET", "/channels")
            bridges = await self.ari("GET", "/bridges")
            if not channels and not bridges:
                return
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"left behind: channels {[c['name'] for c in channels]}, bridges {[b['name'] for b in bridges]}"
                )
            await asyncio.sleep(0.2)

    # ── the brain process ──────────────────────────────────────────────────
    async def start_brain(self):
        log = open(BRAIN_LOG, "a")  # noqa: SIM115
        self.mark()
        self.brain = subprocess.Popen(
            [str(ROOT / ".venv" / "bin" / "python"), "manage.py", "run_brain", "--record", str(self.recording)],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        )
        await self.event(m.BRAIN_RECONCILED, timeout=20)
        say(f"  brain started (pid {self.brain.pid})")

    def start_ai(self):
        log = open(AI_LOG, "a")  # noqa: SIM115
        self.ai = subprocess.Popen(
            [str(ROOT / ".venv" / "bin" / "python"), "manage.py", "run_ai"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT
        )

    def stop_ai(self):
        ai = getattr(self, "ai", None)
        if ai is not None and ai.poll() is None:
            ai.send_signal(signal.SIGINT)
            try:
                ai.wait(10)
            except subprocess.TimeoutExpired:
                ai.kill()

    def kill_brain(self, sig=signal.SIGKILL):
        if self.brain and self.brain.poll() is None:
            self.brain.send_signal(sig)
            self.brain.wait(10)
        self.brain = None


# ── the tests ─────────────────────────────────────────────────────────────
class Lab:
    def __init__(self, world, customer, phone_a, phone_b, agent_a, agent_b, ring_timeout):
        self.w, self.customer, self.phone_a, self.phone_b = world, customer, phone_a, phone_b
        self.a, self.b, self.ring_timeout = agent_a, agent_b, ring_timeout

    async def ready(self, uid, phone):
        self.w.command(m.AGENT_LOGIN, agent=uid, endpoint=f"PJSIP/{phone}")
        self.w.command(m.AGENT_WRAPUP_DONE, agent=uid, code="")  # after the last call, if the queue wraps up
        self.w.command(m.AGENT_READY, agent=uid)
        await self.w.agent_is(uid, availability="ready", activity="idle")

    async def a_first(self):
        """Both Ready and idle, A idle the longest, so A rings first."""
        await self.ready(self.a, "lab-agent-a")
        self.w.command(m.AGENT_LOGIN, agent=self.b, endpoint="PJSIP/lab-agent-b")
        self.w.command(m.AGENT_WRAPUP_DONE, agent=self.b, code="")
        self.w.command(m.AGENT_NOT_READY, agent=self.b, reason="reorder")
        await self.w.agent_is(self.b, availability="not_ready")
        await self.ready(self.b, "lab-agent-b")

    async def dial(self):
        call = await self.customer.dial(LAB_ENTRY)
        await asyncio.wait_for(call.answered.wait(), 10)
        started = await self.w.event(m.CALL_STARTED)
        self.w.dialled.append(started["call"])
        return call, started["call"]

    async def connected(self, timeout=15):
        return await self.w.event(m.CALL_CONNECTED, timeout=timeout)

    async def t1(self):
        """Normal call, customer hangs up -> all legs gone, bridge deleted."""
        self.phone_a.mode = self.phone_b.mode = "answer"
        await self.a_first()
        call, linkedid = await self.dial()
        connected = await self.connected()
        await asyncio.sleep(1)
        await call.hangup()
        ended = await self.w.event(m.CALL_ENDED, call=linkedid)
        assert ended["result"] == "answered", ended
        await self.w.all_clear()
        return f"answered by {connected['agent'][:8]}, customer hung up, nothing left"

    async def t2(self):
        """Normal call, agent hangs up -> the same."""
        self.phone_a.mode = self.phone_b.mode = "answer"
        await self.a_first()
        call, linkedid = await self.dial()
        await self.connected()
        await asyncio.sleep(1)
        answered = [c for p in (self.phone_a, self.phone_b) for c in p.calls.values() if c.answered.is_set() and not c.ended.is_set()]
        assert len(answered) == 1, answered
        await answered[0].hangup()
        await asyncio.wait_for(call.ended.wait(), 5)
        ended = await self.w.event(m.CALL_ENDED, call=linkedid)
        assert ended["reason"] == "agent hung up", ended
        await self.w.all_clear()
        return f"agent hung up; customer got: {call.end_reason}"

    async def t3(self):
        """Customer hangs up while the agent rings -> agent phone stops ringing at once."""
        self.phone_a.mode = self.phone_b.mode = "ignore"
        await self.a_first()
        call, linkedid = await self.dial()
        ringing = await self.phone_a.ringing()
        await asyncio.sleep(0.5)
        hung_up = time.monotonic()
        await call.hangup()
        await asyncio.wait_for(ringing.ended.wait(), 3)
        stopped = time.monotonic() - hung_up
        assert stopped < 1.0, f"agent phone rang on for {stopped:.2f}s"
        ended = await self.w.event(m.CALL_ENDED, call=linkedid)
        assert ended["result"] == "abandoned", ended
        await self.w.all_clear()
        return f"agent phone stopped {stopped * 1000:.0f} ms after the customer hung up ({ringing.end_reason})"

    async def t4(self):
        """Agent rejects -> next agent."""
        self.phone_a.mode, self.phone_b.mode = "reject", "answer"
        await self.a_first()
        call, linkedid = await self.dial()
        failed = await self.w.event(m.CALL_OFFER_FAILED, call=linkedid)
        assert (failed["agent"], failed["reason"]) == (self.a, "rejected"), failed
        connected = await self.connected()
        assert connected["agent"] == self.b, connected
        await call.hangup()
        await self.w.event(m.CALL_ENDED, call=linkedid)
        await self.w.all_clear()
        assert self.w.agent_state(self.a)["availability"] == "ready"
        return f"A rejected (cause {failed['cause']}), B answered, A still Ready"

    async def t5(self):
        """Agent does not answer -> Not responding; the call goes to the next agent."""
        self.phone_a.mode, self.phone_b.mode = "ignore", "answer"
        await self.a_first()
        call, linkedid = await self.dial()
        failed = await self.w.event(m.CALL_OFFER_FAILED, call=linkedid, timeout=self.ring_timeout + 10)
        assert (failed["agent"], failed["reason"]) == (self.a, "no_answer"), failed
        await self.w.agent_is(self.a, activity="not_responding")
        connected = await self.connected()
        assert connected["agent"] == self.b
        await call.hangup()
        await self.w.event(m.CALL_ENDED, call=linkedid)
        await self.w.all_clear()
        return f"A rang {self.ring_timeout}s (cause {failed['cause']}) -> Not responding, B answered"

    async def t6(self):
        """Agent's phone offline -> clean failure, next agent."""
        self.phone_a.mode, self.phone_b.mode = "answer", "answer"
        await self.a_first()
        await self.phone_a.register(expires=0)
        try:
            call, linkedid = await self.dial()
            failed = await self.w.event(m.CALL_OFFER_FAILED, call=linkedid)
            assert (failed["agent"], failed["reason"]) == (self.a, "offline"), failed
            connected = await self.connected()
            assert connected["agent"] == self.b
            await call.hangup()
            await self.w.event(m.CALL_ENDED, call=linkedid)
            await self.w.all_clear()
        finally:
            await self.phone_a.register()
        return "A's phone unregistered: originate failed at once, B answered"

    async def t7(self):
        """Call with unknown args -> logged and hung up."""
        self.customer.mode = "answer"
        channel = await self.w.ari(
            "POST", "/channels", endpoint="PJSIP/lab-customer", app=settings.ARI_APP, appArgs="bogus,args", callerId="lab test 7"
        )
        rejected = await self.w.event(m.STASIS_REJECTED, channel=channel["id"])
        assert rejected["reason"] == "unknown args" and rejected["args"] == ["bogus", "args"], rejected
        await self.w.all_clear()
        return f"StasisStart args {rejected['args']} -> hung up, logged as stasis.rejected"

    async def t8(self):
        """Brain killed mid-call, restarted -> reconcile adopts or cleans."""
        self.phone_a.mode = self.phone_b.mode = "answer"
        await self.a_first()
        call, linkedid = await self.dial()
        await self.connected()
        self.w.kill_brain()
        await asyncio.sleep(1)
        assert not call.ended.is_set(), "the call died with the brain"
        await self.w.start_brain()
        reconciled = await self.w.event(m.BRAIN_RECONCILED)
        assert reconciled["adopted"] == [linkedid], reconciled
        await call.hangup()
        await self.w.event(m.CALL_ENDED, call=linkedid)
        await self.w.all_clear()

        # And a call whose customer left while the brain was dead is cleaned.
        call, linkedid = await self.dial()
        connected = await self.connected()
        self.w.kill_brain()
        await call.hangup()
        await asyncio.sleep(1)
        await self.w.start_brain()
        reconciled = await self.w.event(m.BRAIN_RECONCILED)
        assert reconciled["hung_up"] == [connected["leg"]], reconciled
        await self.w.all_clear()
        return "connected call adopted after SIGKILL; a call that lost its customer meanwhile was cleaned"

    async def t10(self):
        """Ring all (M4): both phones ring through Asterisk, the first answer takes it, the other stops."""
        before = await asyncio.to_thread(set_strategy, "ring_all")
        try:
            await asyncio.sleep(1.5)  # the brain reloads on config.changed
            self.phone_a.mode, self.phone_b.mode = "ignore", "answer"
            self.phone_b.answer_delay = 1.0
            await self.a_first()
            call, linkedid = await self.dial()
            ringing_a = await self.phone_a.ringing()
            ringing_b = await self.phone_b.ringing()
            connected = await self.connected()
            assert connected["agent"] == self.b, connected
            await asyncio.wait_for(ringing_a.ended.wait(), 2)
            assert "cancelled" in ringing_a.end_reason, ringing_a.end_reason
            await call.hangup()
            await self.w.event(m.CALL_ENDED, call=linkedid)
            await self.w.all_clear()
            assert self.w.agent_state(self.a)["activity"] in ("idle", "wrapup"), "ringing out with others is nobody's fault"
        finally:
            self.phone_b.answer_delay = 0.3
            await asyncio.to_thread(set_strategy, before)
            await asyncio.sleep(1.5)
        return f"A and B rang together; B answered, A's phone was cancelled ({ringing_b.end_reason or 'B talked'})"

    async def t11(self):
        """Voicemail (ROUTING_DESIGN T6): max wait -> prompt, the caller's message is recorded, a callback task is asked for."""
        before = await asyncio.to_thread(set_support, overflow_action="voicemail", max_wait=10)
        try:
            await asyncio.sleep(1.5)  # the brain reloads on config.changed
            for uid in (self.a, self.b):  # nobody to take the call
                self.w.command(m.AGENT_WRAPUP_DONE, agent=uid, code="")
                self.w.command(m.AGENT_LOGOUT, agent=uid)
                await self.w.agent_is(uid, availability="logged_out")
            call, linkedid = await self.dial()
            await self.w.event(m.CALL_VOICEMAIL, call=linkedid, timeout=15)
            await asyncio.sleep(9)  # the prompt (about 5 s), the beep, then the customer's tone is recorded
            await call.hangup()
            callback = await self.w.event(m.CALLBACK_REQUESTED, call=linkedid)
            ended = await self.w.event(m.CALL_ENDED, call=linkedid)
            assert ended["result"] == "voicemail" and callback["recording"], (ended, callback)
            async with self.w.http.get(f"{settings.ARI_URL}/recordings/stored/{callback['recording']}/file") as response:
                audio = await response.read()
            assert response.status == 200 and audio[:4] == b"RIFF" and len(audio) > 8000, (response.status, len(audio))
            await self.w.ari("DELETE", f"/recordings/stored/{callback['recording']}")
            await self.w.all_clear()
        finally:
            await asyncio.to_thread(set_support, **before)
            await asyncio.sleep(1.5)
        return f"prompt, then a {callback['duration']} s message ({len(audio)} bytes WAV) and a callback request"

    # ── M5: transfers, each checked in CEL for one linkedid ──────────────
    async def transfer_setup(self):
        """A is on a call with the customer; B is Ready. Returns (customer call, linkedid, A's phone call)."""
        self.phone_a.mode = self.phone_b.mode = "answer"
        await self.a_first()
        call, linkedid = await self.dial()
        connected = await self.connected()
        assert connected["agent"] == self.a
        a_call = await self.answered_call(self.phone_a)
        return call, linkedid, a_call

    async def answered_call(self, phone, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            live = [c for c in phone.calls.values() if c.answered.is_set() and not c.ended.is_set()]
            if live:
                return live[-1]
            await asyncio.sleep(0.1)
        raise AssertionError(f"{phone.user} has no answered call")

    async def one_linkedid(self, linkedid):
        """CEL: every channel of this call carries the customer's linkedid (rule 4)."""
        await asyncio.sleep(1.5)
        legs = {linkedid}
        for event in self.w.new_events():
            if event.data.get("call") == linkedid and event.data.get("leg"):
                legs.add(event.data["leg"])
        seen = {}
        with open(CEL) as cel:
            for row in csv.reader(cel):
                if len(row) > 9 and row[8] in legs:
                    seen.setdefault(row[8], set()).add(row[9])
        linked = set().union(*seen.values()) if seen else set()
        assert len(seen) >= 2 and linked == {linkedid}, (seen, linkedid)
        return len(seen)

    async def t12(self):
        """Blind transfer to an agent: A leaves at once, B gets the customer."""
        call, linkedid, a_call = await self.transfer_setup()
        self.w.command(m.CALL_TRANSFER, agent=self.a, kind="blind", target_kind="agent", target=self.b)
        await asyncio.wait_for(a_call.ended.wait(), 5)
        answered = await self.w.event(m.TRANSFER_COMPLETED, call=linkedid)
        await self.w.agent_is(self.b, activity="talking")
        await asyncio.sleep(1)
        await call.hangup()
        await self.w.event(m.CALL_ENDED, call=linkedid)
        await self.w.all_clear()
        channels = await self.one_linkedid(linkedid)
        return f"A left, B answered (transfer {answered['transfer'][:8]}); {channels} channels, one linkedid in CEL"

    async def consult(self, linkedid):
        self.w.command(m.CALL_TRANSFER, agent=self.a, kind="consult", target_kind="agent", target=self.b)
        await self.w.event(m.TRANSFER_ANSWERED, call=linkedid)
        await self.w.agent_is(self.b, activity="talking")
        await asyncio.sleep(1)

    async def t13(self):
        """Consult, then complete: the customer ends up with B."""
        call, linkedid, a_call = await self.transfer_setup()
        await self.consult(linkedid)
        self.w.command(m.TRANSFER_COMPLETE, agent=self.a)
        await asyncio.wait_for(a_call.ended.wait(), 5)
        await self.w.event(m.TRANSFER_COMPLETED, call=linkedid)
        await asyncio.sleep(1)
        assert not call.ended.is_set()
        await call.hangup()
        await self.w.event(m.CALL_ENDED, call=linkedid)
        await self.w.all_clear()
        return f"consulted B, completed; {await self.one_linkedid(linkedid)} channels, one linkedid in CEL"

    async def t14(self):
        """Consult, then cancel: A is back with the customer, B's phone hung up."""
        call, linkedid, a_call = await self.transfer_setup()
        await self.consult(linkedid)
        b_call = await self.answered_call(self.phone_b)
        self.w.command(m.TRANSFER_CANCEL, agent=self.a)
        await asyncio.wait_for(b_call.ended.wait(), 5)
        await self.w.agent_is(self.a, activity="talking")
        assert not a_call.ended.is_set() and not call.ended.is_set()
        await call.hangup()
        await self.w.event(m.CALL_ENDED, call=linkedid)
        await self.w.all_clear()
        return f"cancelled: A back with the customer; {await self.one_linkedid(linkedid)} channels, one linkedid"

    async def t15(self):
        """Conference: all three together, then A drops B."""
        call, linkedid, a_call = await self.transfer_setup()
        await self.consult(linkedid)
        b_call = await self.answered_call(self.phone_b)
        self.w.command(m.CONFERENCE_START, agent=self.a)
        await self.w.event(m.CONFERENCE_STARTED, call=linkedid)
        await asyncio.sleep(1.5)
        bridges = await self.w.ari("GET", "/bridges")
        assert len(bridges) == 1 and len(bridges[0]["channels"]) == 3, bridges
        self.w.command(m.CONFERENCE_DROP, agent=self.a, who="other")
        await asyncio.wait_for(b_call.ended.wait(), 5)
        assert not a_call.ended.is_set()
        await call.hangup()
        await self.w.event(m.CALL_ENDED, call=linkedid)
        await self.w.all_clear()
        return f"three in one bridge, B dropped; {await self.one_linkedid(linkedid)} channels, one linkedid"

    async def t16(self):
        """Blind transfer to a queue: the call re-enters Support and B takes it."""
        call, linkedid, a_call = await self.transfer_setup()
        self.w.command(m.CALL_TRANSFER, agent=self.a, kind="blind", target_kind="queue", target="Support")
        await asyncio.wait_for(a_call.ended.wait(), 5)
        ticket = await self.w.event(m.ROUTING_TICKET, call=linkedid)
        connected = await self.w.event(m.CALL_CONNECTED, call=linkedid, agent=self.b)
        await asyncio.sleep(1)
        await call.hangup()
        await self.w.event(m.CALL_ENDED, call=linkedid)
        await self.w.all_clear()
        return f"back into {ticket['queue']}, B answered; {await self.one_linkedid(linkedid)} channels, one linkedid"

    async def t17(self):
        """Blind transfer to a number (here B's phone, dialled as a plain number)."""
        call, linkedid, a_call = await self.transfer_setup()
        self.w.command(m.AGENT_LOGOUT, agent=self.b)
        self.w.command(m.CALL_TRANSFER, agent=self.a, kind="blind", target_kind="number", target="lab-agent-b")
        await asyncio.wait_for(a_call.ended.wait(), 5)
        await self.w.event(m.TRANSFER_COMPLETED, call=linkedid)
        b_call = await self.answered_call(self.phone_b)
        await asyncio.sleep(1)
        await b_call.hangup()  # the person called hangs up: the call is over
        await self.w.event(m.CALL_ENDED, call=linkedid)
        await self.w.all_clear()
        return f"PJSIP/lab-agent-b answered and hung up; {await self.one_linkedid(linkedid)} channels, one linkedid"

    # ── M6: supervision, judged by what each phone really hears ──────────
    async def t18(self):
        """Supervisor: monitor, whisper, barge on one call; only the right people hear the supervisor."""
        tones = {"customer": 440, "agent": 700, "supervisor": 1300}
        self.customer.tone_hz, self.phone_a.tone_hz, self.phone_b.tone_hz = tones["customer"], tones["agent"], tones["supervisor"]
        present, absent = 0.05, 0.01
        call = linkedid = None
        try:
            self.phone_a.mode = self.phone_b.mode = "answer"
            await self.ready(self.a, "lab-agent-a")
            self.w.command(m.AGENT_LOGOUT, agent=self.b)  # B's phone is the supervisor's phone now
            await self.w.agent_is(self.b, availability="logged_out")
            call, linkedid = await self.dial()
            await self.connected()
            supervisor = "lab-supervisor"
            self.w.command(m.SUPERVISE_START, supervisor=supervisor, endpoint="PJSIP/lab-agent-b", call=linkedid, mode="monitor")
            await self.w.event(m.SUPERVISE_STARTED, call=linkedid)
            heard = {}
            for mode in ("monitor", "whisper", "barge", "stopped"):
                if mode == "stopped":
                    self.w.command(m.SUPERVISE_STOP, supervisor=supervisor)
                    await self.w.event(m.SUPERVISE_STOPPED, call=linkedid)
                elif mode != "monitor":
                    self.w.command(m.SUPERVISE_MODE, supervisor=supervisor, mode=mode)
                    await self.w.event(m.SUPERVISE_CHANGED, call=linkedid, mode=mode)
                await asyncio.sleep(2.5)  # only audio heard in this mode counts
                heard[mode] = {
                    "agent<-customer": self.phone_a.hears(tones["customer"], 1.5),
                    "customer<-agent": self.customer.hears(tones["agent"], 1.5),
                    "customer<-sup": self.customer.hears(tones["supervisor"], 1.5),
                    "agent<-sup": self.phone_a.hears(tones["supervisor"], 1.5),
                    "sup<-customer": self.phone_b.hears(tones["customer"], 1.5),
                    "sup<-agent": self.phone_b.hears(tones["agent"], 1.5),
                }
                say(f"  {mode:<8} " + "  ".join(f"{who} {level:.3f}" for who, level in heard[mode].items()))
            expected = {  # who must hear the supervisor (True), must not (False); what the supervisor hears
                "monitor": {"customer<-sup": False, "agent<-sup": False, "sup<-customer": True, "sup<-agent": True},
                "whisper": {"customer<-sup": False, "agent<-sup": True, "sup<-customer": True, "sup<-agent": True},
                "barge": {"customer<-sup": True, "agent<-sup": True},
                "stopped": {"customer<-sup": False, "agent<-sup": False},
            }
            wrong = [
                f"{mode}: {who} {heard[mode][who]:.3f}"
                for mode, wants in expected.items()
                for who, should in wants.items()
                if ((heard[mode][who] < present) if should else (heard[mode][who] > absent))
            ]
            assert not wrong, wrong
        finally:
            self.customer.tone_hz = self.phone_a.tone_hz = self.phone_b.tone_hz = 440
            if call is not None and not call.ended.is_set():
                await call.hangup()
        await self.w.event(m.CALL_ENDED, call=linkedid)
        await self.w.all_clear()
        return "monitor: nobody hears the supervisor; whisper: only the agent; barge: everyone (measured at each phone)"

    # ── M7: stereo recording, judged by what is on each channel ──────────
    async def t19(self):
        """Stereo recording: the customer only on the left, what they hear on the right."""
        self.customer.tone_hz, self.phone_a.tone_hz = 440, 700
        try:
            self.phone_a.mode = self.phone_b.mode = "answer"
            await self.a_first()
            call, linkedid = await self.dial()
            await self.connected()
            await asyncio.sleep(3)  # both talk: the customer 440 Hz, agent A 700 Hz
            await call.hangup()
            await self.w.event(m.CALL_ENDED, call=linkedid)
            await self.w.all_clear()
        finally:
            self.customer.tone_hz = self.phone_a.tone_hz = 440
        name = recordings.asterisk_name(linkedid)
        raw = None
        for _ in range(25):  # MixMonitor closes the file as the customer's channel goes
            raw = await asyncio.to_thread(asterisk.fetch_recording, name)
            if raw:
                break
            await asyncio.sleep(0.2)
        assert raw, f"Asterisk has no recording {name}: run `sudo deploy/asterisk/apply.sh m7`"
        with wave.open(io.BytesIO(recordings.stereo_wav(raw))) as wav:
            assert (wav.getnchannels(), wav.getframerate()) == (2, 8000), wav.getparams()
            frames = wav.readframes(wav.getnframes())
        samples = struct.unpack(f"<{len(frames) // 2}h", frames)
        left, right = list(samples[0::2])[-16000:-4000], list(samples[1::2])[-16000:-4000]  # while talking
        shares = {
            "left 440 (customer)": tone_share(left, 440),
            "left 700 (agent)": tone_share(left, 700),
            "right 700 (agent)": tone_share(right, 700),
            "right 440 (customer)": tone_share(right, 440),
        }
        say("  " + "  ".join(f"{what} {share:.3f}" for what, share in shares.items()))
        assert shares["left 440 (customer)"] > 0.5 and shares["left 700 (agent)"] < 0.01, shares
        assert shares["right 700 (agent)"] > 0.5 and shares["right 440 (customer)"] < 0.01, shares
        await asyncio.to_thread(asterisk.delete_recording, name)
        seconds = len(raw) // recordings.FRAME // recordings.RATE
        return f"{name}: {seconds} s stereo; the customer only on the left, the agent only on the right"

    # ── M8: the AI agent, on the real Asterisk with real OpenAI ──────────
    async def until_heard(self, phone, timeout=15, level=300.0):
        """Seconds until ``phone`` hears speech (the AI talking)."""
        started = time.monotonic()
        while phone.loudness(0.3) < level:
            assert time.monotonic() - started < timeout, f"{phone.user} heard nothing for {timeout}s"
            await asyncio.sleep(0.05)
        return time.monotonic() - started

    async def until_quiet(self, phone, timeout=30, level=100.0):
        """Wait until ``phone`` has heard 0.8 s of quiet (the AI finished its sentence)."""
        started = time.monotonic()
        while phone.loudness(0.8) > level:
            assert time.monotonic() - started < timeout, f"{phone.user} still hearing speech after {timeout}s"
            await asyncio.sleep(0.05)

    async def t20(self):
        """AI agent: answers 897, answers one question, hands off on request; the offer carries its summary."""
        question = await voice("What time does your office open on Saturday?")
        person = await voice("I want to talk to a person, please.")
        self.customer.tone_hz = 0  # the customer is quiet except when speaking
        self.w.start_ai()
        try:
            self.phone_a.mode = self.phone_b.mode = "answer"
            await self.a_first()
            await asyncio.sleep(2)  # the worker joins the bus
            call = await self.customer.dial(LAB_AI_ENTRY)
            await asyncio.wait_for(call.answered.wait(), 10)
            linkedid = (await self.w.event(m.CALL_STARTED))["call"]
            self.w.dialled.append(linkedid)
            await self.w.event(m.AI_CONNECTED, call=linkedid, timeout=15)
            greeted = await self.until_heard(self.customer)
            await self.until_quiet(self.customer)
            await asyncio.sleep(self.customer.say(question))
            answered = await self.until_heard(self.customer)
            await self.until_quiet(self.customer)
            await asyncio.sleep(self.customer.say(person))
            offered = await self.w.event(m.CALL_OFFERED, call=linkedid, timeout=30)
            ai = await self.w.event(m.AI_ENDED, call=linkedid)
            say(f"  AI: {ai['outcome']} ({ai['reason']}); summary: {ai['summary']!r}")
            for turn in ai["transcript"]:
                say(f"    {turn['at']:>5} s  {turn['who']:>6}: {turn['text']}")
            assert ai["outcome"] == "handoff" and ai["reason"] == "asked_for_person", ai
            assert offered["summary"] == ai["summary"] and offered["summary"], offered
            assert offered["ai_reason"] == "asked_for_person" and offered["agent"] == self.a
            callers = [turn["text"].lower() for turn in ai["transcript"] if turn["who"] == "caller"]
            assert any("saturday" in text for text in callers) and any("person" in text for text in callers), callers
            assert len([turn for turn in ai["transcript"] if turn["who"] == "ai"]) >= 3, "greeting, answer, hold on"
            await self.connected()
            await call.hangup()
            await self.w.event(m.CALL_ENDED, call=linkedid)
            await self.w.all_clear()
        finally:
            self.customer.tone_hz = 440
            self.customer.speech.clear()
            self.w.stop_ai()
        return (
            f"AI spoke {greeted:.1f}s after answering, replied {answered:.1f}s after the question, "
            f"handed off on request; agent A's offer carried the summary"
        )

    async def t9(self):
        """Brain stopped, customer calls 899 -> the dialplan's Queue() answers."""
        self.w.kill_brain(signal.SIGTERM)
        deadline = time.monotonic() + 10
        while await self.w.brain_connected():
            assert time.monotonic() < deadline, "the app is still registered"
            await asyncio.sleep(0.2)
        call = await self.customer.dial(LAB_ENTRY)
        done = asyncio.ensure_future(call.ended.wait())
        await asyncio.wait([asyncio.ensure_future(call.answered.wait()), done], timeout=10, return_when=asyncio.FIRST_COMPLETED)
        if not call.answered.is_set():
            raise AssertionError(f"not answered ({call.end_reason}): is the fallback in? sudo deploy/asterisk/apply.sh m2")
        await asyncio.sleep(1)
        customers = [c for c in await self.w.ari("GET", "/channels") if c["name"].startswith("PJSIP/lab-customer")]
        apps = [c["dialplan"]["app_name"] for c in customers]
        self.w.dialled += [c["id"] for c in customers]  # recorded too (MixMonitor runs before Stasis)
        await call.hangup()
        assert apps == ["Queue"], f"customer's channel is in {apps}"
        await self.w.all_clear()
        await self.w.start_brain()
        return "no brain: STASISSTATUS=FAILED, Queue(support) answered the customer"


async def main(selected):
    passwords, agent_a, agent_b, ring_timeout = await asyncio.to_thread(ensure_setup)
    headers = {"Authorization": aiohttp.encode_basic_auth(settings.ARI_USER, settings.ARI_PASSWORD)}
    async with aiohttp.ClientSession(headers=headers) as http:
        # The brain appends (it restarts mid-run, test 8); each run starts its recording empty. Only a
        # full run replaces lab.jsonl, so the replayed recording is always one complete, current run.
        recording = RECORDING if sorted(selected) == ALL_TESTS else PARTIAL_RECORDING
        world = World(http, recording)
        if brain_running():
            say("A brain is running on this machine (make dev or make brain). Stop it first (Ctrl-C in its terminal).")
            return 2
        if await world.ari("GET", "/channels"):
            say("Asterisk has live channels; run the tests on a quiet lab.")
            return 2
        recording.parent.mkdir(parents=True, exist_ok=True)
        recording.write_text("")
        customer = await Phone("lab-customer", passwords["lab-customer"]).start()
        phone_a = await Phone("lab-agent-a", passwords["lab-agent-a"]).start()
        phone_b = await Phone("lab-agent-b", passwords["lab-agent-b"]).start()
        results = {}
        try:
            for phone in (customer, phone_a, phone_b):
                assert await phone.register() == 200, f"{phone.user} cannot register"
            await world.start_brain()
            lab = Lab(world, customer, phone_a, phone_b, agent_a, agent_b, ring_timeout)
            for number in selected:
                test = getattr(lab, f"t{number}")
                say(f"── test {number}: {test.__doc__}")
                world.mark()
                for phone in (customer, phone_a, phone_b):  # forget calls from earlier tests
                    while not phone.incoming.empty():
                        phone.incoming.get_nowait()
                try:
                    if world.brain is None:
                        await world.start_brain()
                    detail = await asyncio.wait_for(test(), 90)
                    results[number] = ("PASS", detail)
                except Exception as exc:
                    results[number] = ("FAIL", f"{type(exc).__name__}: {exc}")
                    traceback.print_exc()
                    try:
                        await world.all_clear(timeout=8)
                    except AssertionError as left:
                        say(f"  {left}")
                say(f"  {results[number][0]}: {results[number][1]}")
        finally:
            for uid in (agent_a, agent_b):
                if world.brain is not None:
                    world.command(m.AGENT_LOGOUT, agent=uid)
            await asyncio.sleep(0.5)
            world.kill_brain(signal.SIGTERM)
            for phone in (customer, phone_a, phone_b):
                await phone.close()
            for linkedid in world.dialled:  # no writer here keeps them (M7): clean Asterisk's copies
                await asyncio.to_thread(asterisk.delete_recording, recordings.asterisk_name(linkedid))
        print()
        for number, (verdict, detail) in results.items():
            print(f"  {number}  {verdict}  {detail}")
        print(f"\nBrain log: {BRAIN_LOG}\nRecording: {recording}")
        return 0 if all(verdict == "PASS" for verdict, _ in results.values()) else 1


if __name__ == "__main__":
    chosen = [int(arg) for arg in sys.argv[1:]] or ALL_TESTS
    sys.exit(asyncio.run(main(chosen)))
