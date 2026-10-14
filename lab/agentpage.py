"""M3's "Done when", checked with a real browser: an agent using only Chrome signs in, goes Ready,
takes a call from 800 end to end, holds, wraps up, and every state change is in the Event table.

    cd ~/AlgoDial && .venv/bin/python lab/agentpage.py            # headless
    .venv/bin/python lab/agentpage.py --show                      # watch it in a window

It starts its own stack on this machine: web (ASGI, port 8017), brain, writer and the live
relay, so stop your brain first. The customer is lab/sipphone.py dialling 800; Chrome's
fake microphone plays the agent. Needs `sudo deploy/asterisk/apply.sh m3` (transport-ws).

Lab setup it keeps (idempotent): user lab.agent@example.org (a fresh random password per run)
with the Agent role, agent 9003 in queue Support, wrap-up codes Sale and Complaint, the Not
Ready reason Break.
"""
import argparse
import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
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
from apps.calls.models import Event  # noqa: E402
from apps.core.crypto import decrypt_secret  # noqa: E402
from apps.rbac import services as rbac  # noqa: E402
from apps.telephony.models import SipAccount  # noqa: E402
from contracts import messages as m  # noqa: E402
from sipphone import Phone  # noqa: E402

import agent_login  # noqa: E402

CHECK_AGENT = next(spec for spec in agent_login.AGENTS if spec[0] == "LAB_CHECK_AGENT")

PORT = 8017
EMAIL = agent_login.env_value("LAB_CHECK_AGENT_EMAIL") or CHECK_AGENT[1]
LOG = ROOT / "lab" / "agentpage-stack.log"


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


def port_free(port):
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


class Stack:
    """web (ASGI), brain, writer and live relay, as `make dev` runs them."""

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


async def main(show):
    password, agent_uid, customer_password = await asyncio.to_thread(setup)
    started = datetime.now(timezone.utc)
    if await brain_connected():
        say("A brain is already connected to ARI. Stop it first (and any `make dev`).")
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
            page = await browser.new_page()
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
            call = await customer.dial("800")
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
    sys.exit(asyncio.run(main(parser.parse_args().show)))
