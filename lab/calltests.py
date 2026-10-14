"""The brain's 9 call tests (docs/POC_PLAN.md) against the real Asterisk, with scripted phones.

    cd ~/AlgoDial && .venv/bin/python lab/calltests.py            # all
    .venv/bin/python lab/calltests.py 1 4 8                       # some

What it does:
  * makes sure the lab setup exists: SIP accounts lab-customer, lab-agent-a, lab-agent-b;
    agents 9001 and 9002; queue "Support"; entry point 800 -> Support
  * starts the brain itself (stop yours first: two brains on one app would fight),
    recording every decision to brain/sim/recordings/lab.jsonl
  * plays the customer and both agents with lab/sipphone.py
  * after each test checks, through ARI, that no channel and no bridge is left
    (what `core show channels` and `bridge show all` would say)

Test 9 needs the dialplan fallback: sudo deploy/asterisk/apply.sh m2
Your Zoipers (1001, 1002) are in Queue(support): during test 9 they may ring for a second.
"""
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import traceback
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
from apps.calls import bus  # noqa: E402
from apps.core.crypto import decrypt_secret  # noqa: E402
from apps.queues import services as queues  # noqa: E402
from apps.queues.models import EntryPoint, Queue  # noqa: E402
from apps.telephony import services as telephony  # noqa: E402
from apps.telephony.models import SipAccount  # noqa: E402
from contracts import messages as m  # noqa: E402
from contracts.envelope import decode  # noqa: E402
from sipphone import Phone  # noqa: E402

RECORDING = ROOT / "brain" / "sim" / "recordings" / "lab.jsonl"
BRAIN_LOG = ROOT / "lab" / "calltests-brain.log"


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
    if not EntryPoint.objects.filter(number="800").exists():
        queues.save_entry_point(actor=None, number="800", queue=support, name="Lab entry")
    support.refresh_from_db()
    return passwords, agents["9001"], agents["9002"], support.ring_timeout


# ── the world as Asterisk and the bus see it ───────────────────────────────
class World:
    def __init__(self, http):
        self.http = http
        self.redis = bus.client()
        self.cursor = "$"
        self.seen = []
        self.brain = None

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

    def command(self, kind, **data):
        return bus.send_command(kind, data)

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
        RECORDING.parent.mkdir(parents=True, exist_ok=True)
        log = open(BRAIN_LOG, "a")  # noqa: SIM115
        self.mark()
        self.brain = subprocess.Popen(
            [str(ROOT / ".venv" / "bin" / "python"), "manage.py", "run_brain", "--record", str(RECORDING)],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        )
        await self.event(m.BRAIN_RECONCILED, timeout=20)
        say(f"  brain started (pid {self.brain.pid})")

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
        call = await self.customer.dial("800")
        await asyncio.wait_for(call.answered.wait(), 10)
        started = await self.w.event(m.CALL_STARTED)
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

    async def t9(self):
        """Brain stopped, customer calls 800 -> the dialplan's Queue() answers."""
        self.w.kill_brain(signal.SIGTERM)
        deadline = time.monotonic() + 10
        while await self.w.brain_connected():
            assert time.monotonic() < deadline, "the app is still registered"
            await asyncio.sleep(0.2)
        call = await self.customer.dial("800")
        done = asyncio.ensure_future(call.ended.wait())
        await asyncio.wait([asyncio.ensure_future(call.answered.wait()), done], timeout=10, return_when=asyncio.FIRST_COMPLETED)
        if not call.answered.is_set():
            raise AssertionError(f"not answered ({call.end_reason}): is the fallback in? sudo deploy/asterisk/apply.sh m2")
        await asyncio.sleep(1)
        apps = [c["dialplan"]["app_name"] for c in await self.w.ari("GET", "/channels") if c["name"].startswith("PJSIP/lab-customer")]
        await call.hangup()
        assert apps == ["Queue"], f"customer's channel is in {apps}"
        await self.w.all_clear()
        await self.w.start_brain()
        return "no brain: STASISSTATUS=FAILED, Queue(support) answered the customer"


async def main(selected):
    passwords, agent_a, agent_b, ring_timeout = await asyncio.to_thread(ensure_setup)
    headers = {"Authorization": aiohttp.encode_basic_auth(settings.ARI_USER, settings.ARI_PASSWORD)}
    async with aiohttp.ClientSession(headers=headers) as http:
        world = World(http)
        if await world.brain_connected():
            say("A brain is already connected to ARI as 'algodial'. Stop it first (Ctrl-C in its terminal).")
            return 2
        if await world.ari("GET", "/channels"):
            say("Asterisk has live channels; run the tests on a quiet lab.")
            return 2
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
        print()
        for number, (verdict, detail) in results.items():
            print(f"  {number}  {verdict}  {detail}")
        print(f"\nBrain log: {BRAIN_LOG}\nRecording: {RECORDING}")
        return 0 if all(verdict == "PASS" for verdict, _ in results.values()) else 1


if __name__ == "__main__":
    chosen = [int(arg) for arg in sys.argv[1:]] or list(range(1, 10))
    sys.exit(asyncio.run(main(chosen)))
