"""Lab check for M4b: SIPp plays two carriers on 127.0.0.2 and 127.0.0.3 against the real Asterisk and
the brain of a running `make dev`. No second Asterisk.

    cd ~/AlgoDial && .venv/bin/python lab/carrier_check.py            (make carriercheck)
    .venv/bin/python lab/carrier_check.py --measure                   only measure hangup causes

For the run only it makes (and removes at the end, even after a failure):
  trunks lab-carrier (127.0.0.2:5090, national format out) and lab-carrier-b (127.0.0.3:5090, E.164),
  route "Lab mobiles" (+2557 -> lab-carrier, then lab-carrier-b), DID +255222123456 -> queue
  "Carrier check" whose only agent (2998) takes calls on a scripted desk phone.

1. Measure: which hangup cause Asterisk reports for each carrier answer (POC_PLAN.md M4b, decision C).
2. Check, as in the spec:
   - a carrier call to the DID (as the carrier writes it: 0222123456) reaches the agent;
   - a call to a number that is not a DID is refused by [from-carrier];
   - a call from an address that is not a carrier's is rejected;
   - the agent's transfer to 0712345678 goes out through lab-carrier as 0712345678 and is answered;
   - lab-carrier says 503: the call goes out on lab-carrier-b, in E.164 (+255712345678);
   - lab-carrier says 486 (busy): no failover, lab-carrier-b never sees it.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lab"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django  # noqa: E402

django.setup()

import aiohttp  # noqa: E402
from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.agentdesk import desk  # noqa: E402
from apps.agents import services as agents  # noqa: E402
from apps.agents.live import live_state  # noqa: E402
from apps.agents.models import Agent, AgentSession, WrapupCode  # noqa: E402
from apps.calls import asterisk  # noqa: E402
from apps.calls.models import Event  # noqa: E402
from apps.core.crypto import decrypt_secret  # noqa: E402
from apps.queues import services as queues  # noqa: E402
from apps.queues.models import EntryPoint, Queue  # noqa: E402
from apps.rbac import services as rbac  # noqa: E402
from apps.rbac.models import Permission, Role, RolePermission  # noqa: E402
from apps.telephony import services as telephony  # noqa: E402
from apps.telephony import trunks  # noqa: E402
from apps.telephony.models import OutboundRoute, SipAccount, Trunk  # noqa: E402
from sipphone import Phone  # noqa: E402

SIPP = ROOT / "lab" / "sipp"
DID, DID_AS_SENT, UNKNOWN = "+255222123456", "0222123456", "0222999999"
OUT_TYPED, OUT_NATIONAL, OUT_E164 = "0712345678", "0712345678", "+255712345678"
CARRIER_A, CARRIER_B, STRANGER = "127.0.0.2", "127.0.0.3", "127.0.0.4"
NUMBER, EMAIL, PHONE, QUEUE, ROUTE, ROLE = (
    "2998", "lab.carrier@example.org", "lab-carrier-agent", "Carrier check", "Lab mobiles", "Lab carrier check",
)  # fmt: skip
results: list[tuple[bool, str]] = []


def check(ok, what):
    results.append((bool(ok), what))
    print(f"  {'PASS' if ok else 'FAIL'}  {what}")
    return ok


# ── setup and cleanup ───────────────────────────────────────────────────────
def setup():
    first = trunks.save_trunk(
        actor=None, name="lab-carrier", preset="lab-sipp", hosts=f"{CARRIER_A}:5090", strip_digits=4, prefix="0"
    )
    second = trunks.save_trunk(actor=None, name="lab-carrier-b", preset="lab-sipp", hosts=f"{CARRIER_B}:5090")
    trunks.save_route(actor=None, name=ROUTE, patterns="+2557", trunks=[first, second], caller_id=DID)
    account = SipAccount.objects.filter(sip_id=PHONE).first() or telephony.create_sip_account(
        actor=None, sip_id=PHONE, kind="SOFTPHONE", display_name="Carrier check agent"
    )
    user = get_user_model().all_objects.filter(email=EMAIL).first() or get_user_model().objects.create_user(
        email=EMAIL, password=None, first_name="Carrier", last_name="Check", is_verified=True,
        account_type="Internal", phone="+255700002998",
    )  # fmt: skip
    user.deleted, user.is_active = False, True
    user.save()
    role, _ = Role.objects.get_or_create(name=ROLE, defaults={"description": "lab/carrier_check.py only"})
    for code in ("can_take_calls", "can_dial_external"):
        RolePermission.objects.get_or_create(role=role, permission=Permission.objects.get(code=code))
    rbac.grant_role(user, role)
    agent = Agent.objects.filter(number=NUMBER).first() or agents.create_agent(
        actor=None, number=NUMBER, name="Carrier check", user=user, phone=account
    )
    queue = queues.save_queue(actor=None, name=QUEUE, agents=[agent], ring_timeout=10, max_wait=60)
    if not EntryPoint.objects.filter(number=DID).exists():
        queues.save_entry_point(actor=None, number=DID, queue=queue, name="Carrier check DID")
    WrapupCode.objects.get_or_create(name="Information given")
    return user, agent, decrypt_secret(account.password_encrypted)


def cleanup():
    for entry in EntryPoint.objects.filter(number=DID):
        entry.delete()
    for queue in Queue.objects.filter(name=QUEUE):
        queues.delete_queue(uid=queue.uid)
    for route in OutboundRoute.objects.filter(name=ROUTE):
        route.delete()
    for trunk in Trunk.objects.filter(name__in=("lab-carrier", "lab-carrier-b")):
        trunks.delete_trunk(uid=trunk.uid)
    for agent in Agent.objects.filter(number=NUMBER):
        AgentSession.objects.filter(agent=agent, logout_at=None).update(logout_at=timezone.now())
        agents.delete_agent(uid=agent.uid)
    for account in SipAccount.objects.filter(sip_id=PHONE):
        telephony.delete_sip_account(uid=account.uid)
    for user in get_user_model().objects.filter(email=EMAIL):
        user.delete()
    Role.objects.filter(name=ROLE).delete()


# ── SIPp ────────────────────────────────────────────────────────────────────
class Carrier:
    """SIPp as a carrier taking calls on <ip>:5090: answers (code 200) or refuses with ``code``."""

    def __init__(self, ip: str, code: int | str = 200, scenario: str | None = None):
        self.ip, self.code = ip, str(code)
        folder = Path(tempfile.mkdtemp(prefix="sipp-"))
        self.log = folder / "messages.log"
        if scenario:
            self.scenario = SIPP / scenario
        elif self.code == "200":
            self.scenario = SIPP / "carrier_answers.xml"
        else:  # SIPp checks response codes when it loads a scenario: write the code in
            self.scenario = folder / "carrier_refuses.xml"
            self.scenario.write_text((SIPP / "carrier_refuses.xml").read_text().replace("__CODE__", self.code))
        self.process: asyncio.subprocess.Process | None = None

    async def __aenter__(self):
        self.process = await asyncio.create_subprocess_exec(
            "sipp", "-sf", str(self.scenario), "-i", self.ip, "-p", "5090",
            "-trace_msg", "-message_file", str(self.log), "-nostdin",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        return self

    async def __aexit__(self, *exc):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()

    def invites(self) -> list[str]:
        """The request-URI user of every INVITE this carrier received."""
        text = self.log.read_text(errors="replace") if self.log.exists() else ""
        return re.findall(r"^INVITE sip:([^@\s;]+)@", text, re.MULTILINE)


async def call_in(number: str, *, source: str, hold_ms: int = 4000) -> str:
    """SIPp calls the DID as a carrier at ``source`` would. Returns the final response code it got."""
    log = Path(tempfile.mkdtemp(prefix="sipp-")) / "messages.log"
    process = await asyncio.create_subprocess_exec(
        "sipp", "127.0.0.1:5062", "-sf", str(SIPP / "carrier_calls_in.xml"), "-s", number,
        "-set", "caller", "255754000111", "-i", source, "-p", "5091", "-m", "1", "-d", str(hold_ms),
        "-timeout", "40s", "-trace_msg", "-message_file", str(log), "-nostdin",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    await process.wait()
    text = log.read_text(errors="replace") if log.exists() else ""
    finals = [code for code in re.findall(r"^SIP/2\.0 (\d{3})", text, re.MULTILINE) if code[0] != "1"]
    return finals[0] if finals else "none"


def start_call_in(number: str, *, source: str, hold_ms: int) -> asyncio.Task:
    return asyncio.create_task(call_in(number, source=source, hold_ms=hold_ms))


# ── 1. measuring hangup causes (decision C) ─────────────────────────────────
async def measure_cause(endpoint: str, carrier: Carrier | None) -> tuple[int | None, str]:
    """Originate to the trunk under a separate ARI app (not the brain's) and read ChannelDestroyed."""
    app = "lab-measure"
    url = settings.ARI_URL.replace("http", "ws", 1) + "/events"
    headers = {"Authorization": aiohttp.encode_basic_auth(settings.ARI_USER, settings.ARI_PASSWORD)}
    async with aiohttp.ClientSession(headers=headers) as http, http.ws_connect(url, params={"app": app}) as ws:
        channel = f"measure-{time.time_ns()}"
        response = await http.post(
            f"{settings.ARI_URL}/channels",
            params={"endpoint": endpoint, "app": app, "channelId": channel, "timeout": "45"},
        )
        if response.status >= 400:
            return None, f"originate refused: {response.status} {(await response.text())[:80]}"
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            message = await ws.receive(timeout=deadline - time.monotonic())
            event = json.loads(message.data) if message.type == aiohttp.WSMsgType.TEXT else {}
            if event.get("type") == "StasisStart":  # answered: hang it up, that is not a failure
                await http.delete(f"{settings.ARI_URL}/channels/{channel}")
            if event.get("type") == "ChannelDestroyed" and event["channel"]["id"] == channel:
                return int(event.get("cause", 0)), str(event.get("cause_txt", ""))
    return None, "no ChannelDestroyed in 50 s"


async def wait_reachable(endpoint: str, seconds: float = 45) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if await asyncio.to_thread(asterisk.endpoint_state, endpoint) == "online":
            return True
        await asyncio.sleep(1)
    return False


async def measure() -> dict[str, tuple[int | None, str]]:
    print("Measuring what Asterisk reports for each carrier answer (decision C):")
    found = {}
    async with Carrier(CARRIER_A, 200):
        check(await wait_reachable("trunk-lab-carrier"), "Asterisk sees the SIPp carrier as reachable (qualify)")
    for label, code, scenario in (
        ("503 Service Unavailable", 503, None),
        ("502 Bad Gateway", 502, None),
        ("486 Busy Here", 486, None),
        ("404 Not Found", 404, None),
        ("480 Temporarily Unavailable", 480, None),
        ("603 Decline", 603, None),
        ("no response at all", 200, "carrier_silent.xml"),
    ):
        async with Carrier(CARRIER_A, code, scenario):
            await asyncio.sleep(0.5)
            found[label] = await measure_cause(f"PJSIP/{OUT_NATIONAL}@trunk-lab-carrier", None)
        print(f"    {label:30} -> cause {found[label][0]} ({found[label][1]})")
    found["carrier not running"] = await measure_cause(f"PJSIP/{OUT_NATIONAL}@trunk-lab-carrier", None)
    print(f"    {'carrier not running':30} -> {found['carrier not running']}")
    return found


# ── 2. the spec's checks, through the running brain ─────────────────────────
async def wait_for(test, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if await asyncio.to_thread(test):
            return True
        await asyncio.sleep(0.2)
    return False


def events(kind, since, **data):
    rows = Event.objects.filter(type=kind, at__gte=since)
    for key, value in data.items():
        rows = rows.filter(**{f"data__{key}": value})
    return rows


async def seen(kind, since, **data) -> bool:
    return await asyncio.to_thread(lambda: events(kind, since, **data).exists())


async def back_to_ready(user, agent):
    await wait_for(lambda: live_state(agent)["activity"] in ("wrapup", "idle"), 15)
    if live_state(agent)["activity"] == "wrapup":
        await asyncio.to_thread(desk.finish_wrapup, user, code="Information given", ready=True)
    await wait_for(lambda: live_state(agent)["activity"] == "idle" and live_state(agent)["availability"] == "ready")


async def on_a_call(user, agent, hold_ms):
    """A carrier call to the DID, answered by the agent's desk phone. Returns (since, the SIPp task)."""
    since = timezone.now()
    caller = start_call_in(DID_AS_SENT, source=CARRIER_A, hold_ms=hold_ms)
    talking = await wait_for(lambda: live_state(agent)["activity"] == "talking", 15)
    return since, caller, talking


async def checks(user, agent, password):
    phone = await Phone(PHONE, password, mode="answer").start()
    try:
        await phone.register()
        await asyncio.sleep(1.5)  # the brain reloads its configuration (trunks, route, DID)
        await asyncio.to_thread(desk.start_session, user)
        await wait_for(lambda: live_state(agent)["availability"] != "logged_out")
        await wait_for(lambda: desk.phone_state(user)["state"] == "online", 40)
        await asyncio.to_thread(desk.set_availability, user, ready=True)
        check(await wait_for(lambda: live_state(agent)["availability"] == "ready"), "the check agent is Ready")

        print("Calls in:")
        since, caller, talking = await on_a_call(user, agent, hold_ms=3000)
        check(talking, f"a carrier call to {DID_AS_SENT} reached the agent (DID {DID})")
        check(await seen("call.started", since, entry=DID), f"the brain saw the DID in E.164: {DID}")
        check(await caller == "200", "the carrier's caller was answered (200)")
        await back_to_ready(user, agent)

        since = timezone.now()
        code = await call_in(UNKNOWN, source=CARRIER_A)
        check(code in ("404", "403", "480"), f"a call to {UNKNOWN} (not a DID) was refused: {code}")
        check(not await seen("call.started", since), "it never reached the brain")

        since = timezone.now()
        code = await call_in(DID_AS_SENT, source=STRANGER)
        check(code[0] in "456", f"a call from {STRANGER} (not a carrier) was rejected: {code}")
        check(not await seen("call.started", since), "it never reached the brain")

        print("Calls out (the agent transfers the caller to an outside number):")
        async with Carrier(CARRIER_A, 200) as a, Carrier(CARRIER_B, 200) as b:
            check(await wait_reachable("trunk-lab-carrier"), "both SIPp carriers are reachable")
            since, caller, talking = await on_a_call(user, agent, hold_ms=12000)
            await asyncio.to_thread(desk.transfer, user, kind="blind", target_kind="number", target=OUT_TYPED)
            check(await wait_for(lambda: events("transfer.completed", since).exists(), 15), "the transfer was answered")
            check(a.invites() == [OUT_NATIONAL], f"it went out on lab-carrier as {OUT_NATIONAL}: {a.invites()}")
            check(b.invites() == [], "lab-carrier-b was not used")
            await caller
        await back_to_ready(user, agent)

        async with Carrier(CARRIER_A, 503) as a, Carrier(CARRIER_B, 200) as b:
            await wait_reachable("trunk-lab-carrier")
            await wait_reachable("trunk-lab-carrier-b")
            since, caller, talking = await on_a_call(user, agent, hold_ms=12000)
            await asyncio.to_thread(desk.transfer, user, kind="blind", target_kind="number", target=OUT_TYPED)
            failed_over = await wait_for(lambda: events("trunk.failed_over", since).exists(), 15)
            check(failed_over, "lab-carrier said 503: the brain failed over")
            if failed_over:
                cause = await asyncio.to_thread(lambda: events("trunk.failed_over", since).first().data.get("cause"))
                print(f"    (the failover's cause: {cause})")
            check(await wait_for(lambda: events("transfer.completed", since).exists(), 15), "lab-carrier-b answered")
            check(b.invites() == [OUT_E164], f"lab-carrier-b got it in E.164: {b.invites()}")
            await caller
        await back_to_ready(user, agent)

        async with Carrier(CARRIER_A, 486) as a, Carrier(CARRIER_B, 200) as b:
            await wait_reachable("trunk-lab-carrier")
            since, caller, talking = await on_a_call(user, agent, hold_ms=8000)
            await asyncio.to_thread(desk.transfer, user, kind="blind", target_kind="number", target=OUT_TYPED)
            check(await wait_for(lambda: events("transfer.failed", since).exists(), 15), "486 (busy): the transfer failed")
            check(not await seen("trunk.failed_over", since), "and did not fail over")
            check(b.invites() == [], "lab-carrier-b never saw it")
            await caller
        await back_to_ready(user, agent)
        await asyncio.to_thread(desk.end_session, user)
    finally:
        await phone.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--measure", action="store_true", help="only measure the hangup causes")
    options = parser.parse_args()
    print("M4b carrier check (SIPp on 127.0.0.2 / .3; make dev's brain decides):")
    try:
        cleanup()  # whatever an interrupted run left behind
        user, agent, password = setup()
        asyncio.run(measure())
        if not options.measure:
            asyncio.run(checks(user, agent, password))
    except Exception as exc:  # noqa: BLE001 - report it, then clean up
        check(False, f"stopped: {exc!r}")
    finally:
        cleanup()
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)} of {len(results)} checks passed" + (": FAILED" if failed else "."))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
