"""M3's, M5's and M6's "Done when", checked with real browsers. M3: an agent using only Chrome signs
in, goes Ready, takes a call from 899 (the lab entry) end to end, holds, wraps up, and every state
change is in the Event table. M6: a supervisor in Chrome switches monitor / whisper / barge on that
agent's call, and the customer's phone measures when it hears the supervisor. M5: two agents in two
browsers consult, conference, drop, and transfer blind to an agent, a queue and a number, each by
clicking the page.

    cd ~/AlgoDial && .venv/bin/python lab/agentpage.py            # headless (or: make agentcheck)
    .venv/bin/python lab/agentpage.py --show                      # watch it in a window
    .venv/bin/python lab/agentpage.py --no-transfers --no-supervision   # M3 only

It starts its own stack on this machine: web (ASGI, port 8017), brain, writer and the live
relay, so stop your brain first. The customer is lab/sipphone.py dialling 899; Chrome's
fake microphone plays the agents and the supervisor (a 1300 Hz tone). Needs
`sudo deploy/asterisk/apply.sh m3` (transport-ws) and m4 (entry 899).

Logins come from lab/agent_login.py (passwords in .env): agent 9003 (LAB_CHECK_AGENT_*),
agent 7001 (LAB_AGENT_*, the transfer target) and the lab supervisor (LAB_SUPERVISOR_*).
"""
import argparse
import asyncio
import io
import os
import signal
import socket
import struct
import subprocess
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lab"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")  # this script calls the ORM between awaits

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

from apps.calls import bus  # noqa: E402
from apps.calls.models import Call, Event, RecordingStatus, Transfer  # noqa: E402
from apps.core.crypto import decrypt_secret  # noqa: E402
from apps.rbac import services as rbac  # noqa: E402
from apps.telephony.models import SipAccount  # noqa: E402
from contracts import messages as m  # noqa: E402
from sipphone import Phone, tone_share  # noqa: E402

import agent_login  # noqa: E402

CHECK_AGENT = next(spec for spec in agent_login.AGENTS if spec[0] == "LAB_CHECK_AGENT")

PORT = 8017
EMAIL = agent_login.env_value("LAB_CHECK_AGENT_EMAIL") or CHECK_AGENT[1]
LOG = ROOT / "lab" / "agentpage-stack.log"


def brain_running():
    """Another brain on this machine? (ARI's app list is not proof: Asterisk keeps an app listed while
    a bridge left behind is still subscribed to it, even with nobody connected.)"""
    return subprocess.run(["pgrep", "-f", "manage.py run_brain"], capture_output=True).returncode == 0


def say(text):
    print(f"{time.strftime('%H:%M:%S')}  {text}", flush=True)


def setup():
    """The lab agent (password from .env, see lab/agent_login.py), its queue and the pick lists.

    Returns (password, agent uid, customer password).
    """
    rbac.sync_catalogue()
    agent_login.ensure_pick_lists()
    _email, password, agent = agent_login.ensure_agent(*CHECK_AGENT)
    customer = decrypt_secret(SipAccount.objects.get(sip_id="lab-customer").password_encrypted)
    return password, str(agent.uid), customer


def admin_cookie():
    """A logged-in admin session, as the browser of someone who also uses /admin has."""
    from django.contrib.auth import get_user_model
    from django.test import Client

    client = Client()
    client.force_login(get_user_model().objects.get(email=settings.DEFAULT_ADMIN_EMAIL))
    return client.cookies["sessionid"].value


def second_agent():
    """Agent One (7001) from .env: the transfer target in the M5 phase. Returns (email, password, uid)."""
    spec = next(spec for spec in agent_login.AGENTS if spec[0] == "LAB_AGENT")
    email, password, agent = agent_login.ensure_agent(*spec)
    return email, password, str(agent.uid)


async def signed_in(browser, email, password, admin_session):
    context = await browser.new_context()
    await context.add_cookies([{"name": "sessionid", "value": admin_session, "url": f"http://127.0.0.1:{PORT}"}])
    page = await context.new_page()
    page.on("console", lambda message: message.type == "error" and say(f"  browser ({email}): {message.text}"))
    await page.goto(f"http://127.0.0.1:{PORT}/agent/")
    await page.fill("#email", email)
    await page.fill("#password", password)
    await page.click("button[type=submit]")
    await page.wait_for_selector("#desk:not([hidden])", timeout=10000)
    await until(page, "document.getElementById('phone-chip').textContent.startsWith('Phone: web')", 15)
    await until(page, "document.getElementById('live-chip').textContent === 'Live: on'", 10)
    return page


async def activity(page):
    return await page.evaluate("window.agentDesk.state && window.agentDesk.state.activity")


async def done_wrapping_up(page):
    # Right after a hangup the page may still say talking; wait for the brain's next state first.
    await until(page, "!['talking', 'on_hold', 'offered'].includes(window.agentDesk.state.activity)", 10)
    if await activity(page) == "wrapup":
        await page.click("#wrapup-done")
    await until(page, "window.agentDesk.state.activity !== 'wrapup'", 10)


async def take_call(customer, page, number="899"):
    """A customer calls; the page that rings answers it."""
    call = await customer.dial(number)
    await asyncio.wait_for(call.answered.wait(), 10)
    await page.wait_for_selector("#call:not([hidden])", timeout=15000)
    await page.click("#answer")
    await until(page, "window.agentDesk.state.activity === 'talking'", 15)
    return call


async def transfer_from(page, kind, target_kind, target):
    await page.select_option("#transfer-kind", kind)
    await page.select_option("#transfer-target-kind", target_kind)
    if target_kind == "agent":
        await until(page, f"[...document.getElementById('transfer-agent').options].some(o => o.value === '{target}')", 10)
        await page.select_option("#transfer-agent", target)
    elif target_kind == "queue":
        await page.select_option("#transfer-queue", target)
    else:
        await page.fill("#transfer-number", target)
    await page.click("#transfer-go")


async def bridges():
    headers = {"Authorization": aiohttp.encode_basic_auth(settings.ARI_USER, settings.ARI_PASSWORD)}
    async with aiohttp.ClientSession(headers=headers) as http, http.get(settings.ARI_URL + "/bridges") as response:
        return await response.json()


async def transfers_from_the_page(playwright, show, customer, admin_session, check, password):
    """M5's Done-when: each transfer flow works from the agent page (two agents in two browsers)."""
    other_email, other_password, other_uid = await asyncio.to_thread(second_agent)
    browser = await playwright.chromium.launch(
        channel="chrome",
        headless=not show,
        args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
    )
    first = await signed_in(browser, EMAIL, password, admin_session)
    second = await signed_in(browser, other_email, other_password, admin_session)
    await first.click("#ready")
    await until(first, "document.getElementById('availability').textContent === 'Ready'", 10)
    check("two agents signed in, two browser phones", True, "9003 Ready, 7001 Not Ready")

    say("── consult, conference, drop the other party")
    call = await take_call(customer, first)
    await transfer_from(first, "consult", "agent", other_uid)
    await second.wait_for_selector("#call:not([hidden])", timeout=15000)
    check("the consulted agent's page shows the transfer", "Transfer" in (await second.inner_text("#caller")), await second.inner_text("#caller-queue"))
    await second.click("#answer")
    await until(first, "!document.getElementById('consult-complete').disabled", 15)
    check("the consulting agent sees Complete / Conference / Cancel", True)
    await first.click("#consult-conference")
    await until(first, "!document.getElementById('conference').hidden", 10)
    deadline = time.monotonic() + 10
    while not any(len(b["channels"]) == 3 for b in await bridges()):
        assert time.monotonic() < deadline, await bridges()
        await asyncio.sleep(0.2)
    check("conference: customer and both agents in one bridge", True)
    await first.click("#conference-drop")
    await until(second, "window.agentDesk.session === null", 10)
    check("drop the other party: their call ends, the customer stays", not call.ended.is_set())
    await call.hangup()
    await done_wrapping_up(first)
    await done_wrapping_up(second)

    say("── blind transfer to an agent")
    call = await take_call(customer, first)
    await transfer_from(first, "blind", "agent", other_uid)
    await until(first, "window.agentDesk.session === null", 10)
    await second.wait_for_selector("#call:not([hidden])", timeout=15000)
    await second.click("#answer")
    await until(second, "window.agentDesk.state.activity === 'talking'", 15)
    check("blind to an agent: the first agent is free at once, the second talks to the customer", not call.ended.is_set())
    await call.hangup()
    await done_wrapping_up(first)
    await done_wrapping_up(second)

    say("── blind transfer to a queue")
    call = await take_call(customer, first)
    await second.click("#ready")
    await until(second, "document.getElementById('availability').textContent === 'Ready'", 10)
    await transfer_from(first, "blind", "queue", "Support")
    await until(first, "window.agentDesk.session === null", 10)
    await second.wait_for_selector("#call:not([hidden])", timeout=20000)
    await second.click("#answer")
    await until(second, "window.agentDesk.state.activity === 'talking'", 15)
    check("blind to a queue: the call went back into Support and the free agent took it", not call.ended.is_set())
    await call.hangup()
    await done_wrapping_up(first)
    await done_wrapping_up(second)

    say("── blind transfer to a number")
    outside = await Phone("lab-agent-b", await asyncio.to_thread(sip_password, "lab-agent-b"), mode="answer").start()
    assert await outside.register() == 200
    try:
        await second.click("#not-ready")
        call = await take_call(customer, first)
        await transfer_from(first, "blind", "number", "lab-agent-b")
        await until(first, "window.agentDesk.session === null", 10)
        answered = await outside.ringing(timeout=15)
        await asyncio.wait_for(answered.answered.wait(), 5)
        check("blind to a number: the person called talks to the customer", not call.ended.is_set())
        await answered.hangup()
        await asyncio.wait_for(call.ended.wait(), 5)
    finally:
        await outside.close()
    await done_wrapping_up(first)
    for page in (first, second):
        await page.click("#signout")
    await browser.close()


def supervisor_tone_file():
    """A WAV of a steady 1300 Hz tone: the supervisor's "voice" (Chrome's fake microphone plays it)."""
    import math
    import struct
    import tempfile
    import wave

    path = Path(tempfile.gettempdir()) / "algodial-supervisor-1300hz.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 1300 * n / 16000))) for n in range(16000 * 5)))
    return str(path)


async def supervision_from_the_page(playwright, show, customer, admin_session, check, password):
    """M6's Done-when: a supervisor in the browser switches monitor, whisper and barge on one call,
    and only the right people hear them (the customer hears the supervisor's tone only in barge)."""
    email, sup_password = await asyncio.to_thread(agent_login.ensure_supervisor)
    tone = supervisor_tone_file()
    agent_browser = await playwright.chromium.launch(
        channel="chrome",
        headless=not show,
        args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
    )
    sup_browser = await playwright.chromium.launch(
        channel="chrome",
        headless=not show,
        args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-audio-capture={tone}",
            "--autoplay-policy=no-user-gesture-required",
        ],
    )
    agent = await signed_in(agent_browser, EMAIL, password, admin_session)
    await agent.click("#ready")
    await until(agent, "document.getElementById('availability').textContent === 'Ready'", 10)

    context = await sup_browser.new_context()
    sup = await context.new_page()
    sup.on("console", lambda message: message.type == "error" and say(f"  browser (supervisor): {message.text}"))
    await sup.goto(f"http://127.0.0.1:{PORT}/supervisor/")
    await sup.fill("#email", email)
    await sup.fill("#password", sup_password)
    await sup.click("button[type=submit]")
    await sup.wait_for_selector("#desk:not([hidden])", timeout=10000)
    await until(sup, "document.getElementById('phone-chip').textContent.startsWith('Phone: websup')", 15)
    await until(sup, "document.getElementById('live-chip').textContent === 'Live: on'", 10)
    check("supervisor signed in with a browser phone and the live view", True, await sup.inner_text("#me"))

    customer.tone_hz = 440
    call = await take_call(customer, agent)
    await until(sup, "window.supervisorDesk.calls.some(c => c.state === 'connected')", 10)
    check("the supervisor sees the call live", True)

    async def switch(mode, button):
        await sup.click(button)
        await until(sup, f"window.supervisorDesk.listening && window.supervisorDesk.listening.mode === '{mode}'", 20)
        await until(sup, "window.supervisorDesk.session && window.supervisorDesk.session.isEstablished()", 10)
        await asyncio.sleep(2.5)  # only audio heard in this mode counts
        return customer.hears(1300, 1.5)

    await sup.click("#calls button:text('Monitor')")
    await until(sup, "window.supervisorDesk.listening && window.supervisorDesk.listening.mode === 'monitor'", 20)
    await until(sup, "window.supervisorDesk.session && window.supervisorDesk.session.isEstablished()", 10)
    await asyncio.sleep(2.5)
    monitor = customer.hears(1300, 1.5)
    whisper = await switch("whisper", "#mode-whisper")
    barge = await switch("barge", "#mode-barge")
    say(f"  the customer hears the supervisor's tone: monitor {monitor:.3f}, whisper {whisper:.3f}, barge {barge:.3f}")
    check("monitor: the customer does not hear the supervisor", monitor < 0.01, f"{monitor:.3f}")
    check("whisper: the customer does not hear the supervisor", whisper < 0.01, f"{whisper:.3f}")
    check("barge: the customer hears the supervisor", barge > 0.05, f"{barge:.3f}")
    await sup.click("#stop")
    await until(sup, "window.supervisorDesk.listening === null", 10)
    check("stop: the supervisor's phone call ends, the call goes on", not call.ended.is_set())
    await call.hangup()
    await done_wrapping_up(agent)
    await agent.click("#signout")
    await sup.click("#signout")
    await agent_browser.close()
    await sup_browser.close()


def sip_password(sip_id):
    return decrypt_secret(SipAccount.objects.get(sip_id=sip_id).password_encrypted)


def port_free(port):
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


class Stack:
    """web (ASGI), brain, writer, live relay, recordings keeper and AI worker, as `make dev` runs them."""

    def __init__(self):
        self.processes = []

    def start(self):
        log = open(LOG, "w")  # noqa: SIM115
        python = str(ROOT / ".venv" / "bin" / "python")
        for args in (
            ["runserver", f"127.0.0.1:{PORT}", "--noreload"],
            ["run_brain"],
            ["run_writer", "--consumer", "writer-1"],
            ["run_live"],
            ["run_recordings"],
            ["run_ai"],
        ):
            self.processes.append(
                subprocess.Popen([python, "manage.py", *args], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            )

    def stop(self):
        for process in self.processes:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
        for process in self.processes:
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()


async def wait_http(url, timeout=30):
    deadline = time.monotonic() + timeout
    async with aiohttp.ClientSession() as http:
        while time.monotonic() < deadline:
            try:
                async with http.get(url) as response:
                    if response.status == 200:
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.3)
    raise AssertionError(f"{url} never answered")


async def brain_connected():
    headers = {"Authorization": aiohttp.encode_basic_auth(settings.ARI_USER, settings.ARI_PASSWORD)}
    async with aiohttp.ClientSession(headers=headers) as http, http.get(settings.ARI_URL + "/applications") as response:
        return any(app["name"] == settings.ARI_APP for app in await response.json())


async def until(page, expression, timeout=10):
    """Poll a JS expression through DevTools (not subject to the page's CSP, unlike wait_for_function)."""
    deadline = time.monotonic() + timeout
    while not await page.evaluate(expression):
        if time.monotonic() > deadline:
            raise AssertionError(f"never true in the page: {expression}")
        await asyncio.sleep(0.1)


async def media_stats(page):
    """Packets the browser sent and received on the current call."""
    return await page.evaluate(
        """async () => {
            const session = window.agentDesk.session;
            if (!session || !session.connection) return null;
            const out = { received: 0, sent: 0 };
            (await session.connection.getStats()).forEach((report) => {
                if (report.type === "inbound-rtp" && report.kind === "audio") out.received += report.packetsReceived || 0;
                if (report.type === "outbound-rtp" && report.kind === "audio") out.sent += report.packetsSent || 0;
            });
            return out;
        }"""
    )


async def ai_from_the_page(playwright, show, customer, admin_session, check, password):
    """M8's Done-when: the AI answers, handles one question and hands off on request; the agent sees
    the summary, and the interaction shows both parts (the lab AI agent on entry 897, real OpenAI)."""
    import calltests
    from apps.calls.models import InteractionPart, PartKind
    from apps.queues.models import Queue

    await asyncio.to_thread(lambda: calltests.ensure_lab_ai(Queue.objects.get(name__iexact="Support")))
    question = await calltests.voice("What time does your office open on Saturday?")
    person = await calltests.voice("I want to talk to a person, please.")

    async def heard(level=300.0, timeout=20):
        started = time.monotonic()
        while customer.loudness(0.3) < level:
            assert time.monotonic() - started < timeout, "the caller heard nothing from the AI"
            await asyncio.sleep(0.05)

    async def quiet(timeout=30):
        started = time.monotonic()
        while customer.loudness(0.8) > 100:
            assert time.monotonic() - started < timeout, "the AI never stopped talking"
            await asyncio.sleep(0.05)

    browser = await playwright.chromium.launch(
        channel="chrome",
        headless=not show,
        args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
    )
    page = await signed_in(browser, EMAIL, password, admin_session)
    await page.click("#ready")
    await until(page, "document.getElementById('availability').textContent === 'Ready'", 10)
    customer.tone_hz = 0
    started = datetime.now(timezone.utc)
    try:
        call = await customer.dial(calltests.LAB_AI_ENTRY)
        await asyncio.wait_for(call.answered.wait(), 10)
        await heard()
        await quiet()
        await asyncio.sleep(customer.say(question))
        await heard()
        await quiet()
        check("the AI greeted the caller and answered the question", True)
        await asyncio.sleep(customer.say(person))
        await page.wait_for_selector("#ai-summary:not([hidden])", timeout=30000)
        summary = await page.inner_text("#ai-summary-text")
        reason = await page.inner_text("#ai-reason")
        check(
            "the agent's incoming call shows the AI's summary",
            "saturday" in summary.lower() and reason == "asked for a person",
            f"{reason}: {summary}",
        )
        await page.click("#answer")
        await until(page, "window.agentDesk.state.activity === 'talking'", 15)
        check("the agent talks with the caller the AI handed over", not call.ended.is_set())
        await call.hangup()
        await until(page, "window.agentDesk.state.activity === 'wrapup'", 10)
        await page.select_option("#wrapup-code", "Information given")
        await page.click("#wrapup-done")
        await until(page, "window.agentDesk.state.activity !== 'wrapup'", 10)
    finally:
        customer.tone_hz = 440
        customer.speech.clear()

    deadline = time.monotonic() + 10
    while True:
        rows = await asyncio.to_thread(
            lambda: list(
                InteractionPart.objects.filter(call__entry=calltests.LAB_AI_ENTRY, started_at__gte=started)
                .select_related("call")
                .order_by("started_at")
            )
        )
        if len(rows) == 2 and rows[1].wrapup_code or time.monotonic() > deadline:
            break
        await asyncio.sleep(0.5)
    kinds = [row.kind for row in rows]
    ai_part = rows[0] if rows else None
    callers = [turn["text"] for turn in ai_part.transcript if turn["who"] == "caller"] if ai_part else []
    check(
        "the interaction has both parts: the AI (summary, transcript), then the agent (wrap-up code)",
        kinds == [PartKind.AI, PartKind.AGENT]
        and ai_part.outcome == "handoff"
        and len(callers) == 2
        and rows[1].wrapup_code == "Information given",
        f"{kinds}; caller said {callers}; wrap-up {rows[1].wrapup_code if len(rows) > 1 else '-'}",
    )
    if rows:
        async with aiohttp.ClientSession(cookies={"sessionid": admin_session}) as http:
            async with http.get(f"http://127.0.0.1:{PORT}/admin/calls/call/{rows[0].call.pk}/change/") as response:
                html = await response.text()
        check(
            "the call's admin page shows the interaction and the transcript",
            "Interaction" in html and "Saturday" in html and "Information given" in html,
        )
    await page.click("#signout")
    await browser.close()


async def main(show, transfers=True, supervision=True, ai=True):
    password, agent_uid, customer_password = await asyncio.to_thread(setup)
    admin_session = await asyncio.to_thread(admin_cookie)
    started = datetime.now(timezone.utc)
    if brain_running():
        say("A brain is running on this machine (make dev or make brain). Stop it first.")
        return 2
    if not port_free(PORT):
        say(f"Port {PORT} is taken.")
        return 2

    stack = Stack()
    stack.start()
    customer = await Phone("lab-customer", customer_password).start()
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))
        say(f"  {'PASS' if ok else 'FAIL'}  {name} {detail}")
        if not ok:
            raise AssertionError(name)

    try:
        await wait_http(f"http://127.0.0.1:{PORT}/healthz")
        deadline = time.monotonic() + 20
        while not await brain_connected():
            assert time.monotonic() < deadline, "the brain never connected"
            await asyncio.sleep(0.3)
        assert await customer.register() == 200
        # Start from logged out, whatever an earlier run left behind.
        bus.send_command(m.AGENT_LOGOUT, {"agent": agent_uid})
        deadline = time.monotonic() + 10
        while (bus.client().hget(settings.BUS_AGENTS_KEY, agent_uid) or b"").find(b'"logged_out"') < 0:
            if not bus.client().hexists(settings.BUS_AGENTS_KEY, agent_uid) or time.monotonic() > deadline:
                break
            await asyncio.sleep(0.2)
        started = datetime.now(timezone.utc)

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                channel="chrome",
                headless=not show,
                args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
            )
            context = await browser.new_context()
            # Like a real browser: someone is also signed in to the admin on this host. The page
            # must still act as the agent of its own token, never as the cookie's user.
            await context.add_cookies([{"name": "sessionid", "value": admin_session, "url": f"http://127.0.0.1:{PORT}"}])
            page = await context.new_page()
            page.on("console", lambda message: message.type == "error" and say(f"  browser: {message.text}"))
            await page.goto(f"http://127.0.0.1:{PORT}/agent/")

            say("── sign in")
            await page.fill("#email", EMAIL)
            await page.fill("#password", password)
            await page.click("button[type=submit]")
            await page.wait_for_selector("#desk:not([hidden])", timeout=10000)
            check("signed in with the OAuth token", True, await page.inner_text("#agent-name"))
            await until(page, "document.getElementById('phone-chip').textContent.includes('web9003')", 15)
            check("browser phone registered over WebSocket", True, await page.inner_text("#phone-chip"))
            await until(page, "document.getElementById('live-chip').textContent === 'Live: on'", 10)
            check("live WebSocket open", True)

            say("── Ready")
            await page.click("#ready")
            await until(page, "document.getElementById('availability').textContent === 'Ready'", 10)
            check("agent is Ready (state pushed by the brain)", True)

            say("── a customer calls 800")
            call = await customer.dial("899")
            await asyncio.wait_for(call.answered.wait(), 10)
            await page.wait_for_selector("#call:not([hidden])", timeout=15000)
            check("offer popup shows", True, (await page.inner_text("#caller")) + " " + await page.inner_text("#caller-queue"))
            await page.click("#answer")
            await until(page, "window.agentDesk.state && window.agentDesk.state.activity === 'talking'", 15)
            check("answered: talking", True)
            before = customer.rtp_packets
            await asyncio.sleep(3)
            stats = await media_stats(page)
            heard = customer.rtp_packets - before
            say(f"  media: browser sent {stats and stats['sent']}, received {stats and stats['received']}; customer got {heard}")
            check("the customer's voice reaches the browser", stats and stats["received"] > 20, str(stats))
            check("the agent's microphone reaches the customer", heard > 20, f"{heard} RTP packets in 3 s")


            say("── hold and resume")
            await page.click("#hold")
            await until(page, "window.agentDesk.state.activity === 'on_hold'", 10)
            check("on hold", True)
            await page.click("#resume")
            await until(page, "window.agentDesk.state.activity === 'talking'", 10)
            check("resumed", True)

            say("── the customer hangs up; wrap-up")
            await call.hangup()
            await page.wait_for_selector("#wrapup:not([hidden])", timeout=10000)
            check("wrap-up shows", True)
            await page.select_option("#wrapup-code", "Sale")
            await page.click("#wrapup-done")
            await until(page, "window.agentDesk.state.activity === 'idle'", 10)
            check("wrapped up with a code, idle again", True)

            say("── Not Ready, then sign out")
            await page.select_option("#reason", "Break")
            await page.click("#not-ready")
            await until(page, "document.getElementById('activity').textContent.includes('Break')", 10)
            check("Not Ready with a reason", True)
            await page.click("#signout")
            await page.wait_for_selector("#signin:not([hidden])", timeout=10000)
            check("signed out", True)
            await browser.close()

        say("── the Event table")
        await asyncio.sleep(2)  # the writer runs on its own

        def events():
            rows = Event.objects.filter(agent_uid=agent_uid, at__gte=started).order_by("at", "id")
            return [(row.type, row.data) for row in rows]

        rows = await asyncio.to_thread(events)
        states = [(data["availability"], data["activity"]) for kind, data in rows if kind == "agent.state"]
        wanted = [
            ("not_ready", "idle"),
            ("ready", "idle"),
            ("ready", "offered"),
            ("ready", "talking"),
            ("ready", "on_hold"),
            ("ready", "talking"),
            ("ready", "wrapup"),
            ("ready", "idle"),
            ("not_ready", "idle"),
            ("logged_out", "idle"),
        ]
        check("every state change is in the Event table", states == wanted, "\n      " + "\n      ".join(map(str, states)))
        wrapped = [data for kind, data in rows if kind == "agent.wrapped_up"]
        check("the wrap-up code is in the Event table", wrapped and wrapped[-1]["code"] == "Sale", str(wrapped))
        kinds = {kind for kind, _ in rows}
        check("the call's events are there too", {"call.offered", "call.connected", "call.held", "call.resumed", "call.ended"} <= kinds)

        say("── M7: the call's stereo recording")
        linkedid = wrapped[-1]["call"] if wrapped else ""
        deadline = time.monotonic() + 20
        recorded = None
        while time.monotonic() < deadline:
            recorded = await asyncio.to_thread(Call.objects.filter(linkedid=linkedid).first)
            if recorded and recorded.recording_status in (RecordingStatus.READY, RecordingStatus.MISSING):
                break
            await asyncio.sleep(0.5)
        state = recorded.recording_status if recorded else "no Call row"
        check("the recording was kept after the call", state == RecordingStatus.READY, f"{state} (needs apply.sh m7)")
        if state == RecordingStatus.READY:
            cookies = {"sessionid": admin_session}
            async with aiohttp.ClientSession(cookies=cookies) as http:
                async with http.get(f"http://127.0.0.1:{PORT}/calls/{recorded.pk}/recording") as response:
                    audio = await response.read() if response.status == 200 else b""
            with wave.open(io.BytesIO(audio)) as wav:
                stereo = wav.getnchannels() == 2
                frames = wav.readframes(wav.getnframes())
            samples = struct.unpack(f"<{len(frames) // 2}h", frames)
            left, right = tone_share(samples[0::2], 440), tone_share(samples[1::2], 440)
            check(
                "it plays from the admin, the customer's tone only on the left",
                stereo and left > 0.5 and right < 0.05,
                f"{recorded.recording_seconds} s, 440 Hz share: left {left:.3f}, right {right:.3f}",
            )

        if supervision:
            async with async_playwright() as playwright:
                await supervision_from_the_page(playwright, show, customer, admin_session, check, password)

        if ai:
            say("── M8: the AI agent answers, then hands over to the agent")
            async with async_playwright() as playwright:
                await ai_from_the_page(playwright, show, customer, admin_session, check, password)

        if transfers:
            transfers_started = datetime.now(timezone.utc)
            async with async_playwright() as playwright:
                await transfers_from_the_page(playwright, show, customer, admin_session, check, password)
            await asyncio.sleep(2)  # the writer
            statuses = await asyncio.to_thread(
                lambda: list(Transfer.objects.filter(started_at__gte=transfers_started).order_by("started_at").values_list("kind", "target_kind", "status"))
            )
            check(
                "every transfer is recorded with its status",
                statuses == [("consult", "agent", "conference"), ("blind", "agent", "completed"), ("blind", "queue", "completed"), ("blind", "number", "completed")],
                str(statuses),
            )
    except Exception as exc:
        if not checks or checks[-1][1]:
            say(f"  FAIL  {type(exc).__name__}: {exc}")
        return 1
    finally:
        await customer.close()
        stack.stop()
        say(f"Stack log: {LOG}")
    say(f"All {len(checks)} checks passed.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--show", action="store_true", help="open a visible Chrome window")
    parser.add_argument("--no-transfers", action="store_true", help="skip the M5 transfer phase")
    parser.add_argument("--no-supervision", action="store_true", help="skip the M6 supervisor phase")
    parser.add_argument("--no-ai", action="store_true", help="skip the M8 AI agent phase")
    arguments = parser.parse_args()
    sys.exit(
        asyncio.run(
            main(
                arguments.show,
                transfers=not arguments.no_transfers,
                supervision=not arguments.no_supervision,
                ai=not arguments.no_ai,
            )
        )
    )
