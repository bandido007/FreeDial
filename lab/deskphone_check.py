"""Lab check: an agent whose Phone (admin -> Agents -> Phone) is a desk phone gets the call on that phone.

    cd ~/AlgoDial && .venv/bin/python lab/deskphone_check.py      (make dev must be running: its brain decides)

Real Asterisk, real brain, scripted phones (lab/sipphone.py). For the run only it makes: a desk phone
"lab-desk" and a caller "lab-desk-caller" (SIP accounts), a person and agent 2999 whose Phone is lab-desk,
queue "Desk phone check" with only that agent, and entry 898 to it. Then:

  1. lab-desk registers; the agent signs in: AlgoDial says the phone is online, makes no browser phone;
  2. the agent goes Ready; lab-desk-caller dials 898;
  2b. the page's re-check sees Asterisk report the new phone online (up to 30 s);
  3. the brain rings PJSIP/lab-desk (not web2999), the desk phone answers, the call connects;
  4. the agent wraps up and signs out: lab-desk stays switched on.

Everything it made is removed at the end, even after a failure.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lab"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.agentdesk import desk  # noqa: E402
from apps.agents import services as agents  # noqa: E402
from apps.agents.live import live_state  # noqa: E402
from apps.agents.models import Agent, AgentSession, WrapupCode  # noqa: E402
from apps.calls.models import Event  # noqa: E402
from apps.core.crypto import decrypt_secret  # noqa: E402
from apps.queues import services as queues  # noqa: E402
from apps.queues.models import EntryPoint, Queue  # noqa: E402
from apps.rbac import selectors as rbac_selectors  # noqa: E402
from apps.rbac import services as rbac  # noqa: E402
from apps.telephony import services as telephony  # noqa: E402
from apps.telephony.models import SipAccount  # noqa: E402
from sipphone import Phone  # noqa: E402

DESK, CALLER, ENTRY, NUMBER = "lab-desk", "lab-desk-caller", "898", "2999"
EMAIL, QUEUE = "lab.deskphone@example.org", "Desk phone check"
results: list[tuple[bool, str]] = []


def check(ok, what):
    results.append((bool(ok), what))
    print(f"  {'PASS' if ok else 'FAIL'}  {what}")
    return ok


def setup():
    accounts = {}
    for sip_id in (DESK, CALLER):
        account = SipAccount.objects.filter(sip_id=sip_id).first() or telephony.create_sip_account(
            actor=None, sip_id=sip_id, kind="SOFTPHONE", display_name=sip_id
        )
        accounts[sip_id] = account
    user = get_user_model().all_objects.filter(email=EMAIL).first()  # an earlier run's, switched off
    if user is None:
        user = get_user_model().objects.create_user(
            email=EMAIL, password=None, first_name="Desk", last_name="Check", is_verified=True,
            account_type="Internal", phone="+255700002999",
        )
    user.deleted, user.is_active = False, True
    user.save()
    rbac.grant_role(user, rbac_selectors.get_role_by_name("Agent"))
    agent = Agent.objects.filter(number=NUMBER).first() or agents.create_agent(
        actor=None, number=NUMBER, name="Desk check", user=user, phone=accounts[DESK]
    )
    queue = queues.save_queue(actor=None, name=QUEUE, agents=[agent], ring_timeout=10, max_wait=30)
    if not EntryPoint.objects.filter(number=ENTRY).exists():
        queues.save_entry_point(actor=None, number=ENTRY, queue=queue, name="Desk phone check")
    WrapupCode.objects.get_or_create(name="Information given")
    return user, agent, {k: decrypt_secret(a.password_encrypted) for k, a in accounts.items()}


def cleanup():
    for entry in EntryPoint.objects.filter(number=ENTRY):
        entry.delete()
    for queue in Queue.objects.filter(name=QUEUE):
        queues.delete_queue(uid=queue.uid)
    for agent in Agent.objects.filter(number=NUMBER):
        AgentSession.objects.filter(agent=agent, logout_at=None).update(logout_at=timezone.now())
        agents.delete_agent(uid=agent.uid)
    for user in get_user_model().objects.filter(email=EMAIL):
        user.delete()
    for account in SipAccount.objects.filter(sip_id__in=(DESK, CALLER, f"web{NUMBER}")):
        telephony.delete_sip_account(uid=account.uid)


async def wait_for(test, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if await asyncio.to_thread(test):
            return True
        await asyncio.sleep(0.2)
    return False


async def run(user, agent, passwords):
    phone = await Phone(DESK, passwords[DESK], mode="answer").start()
    caller = await Phone(CALLER, passwords[CALLER]).start()
    try:
        await phone.register()
        await caller.register()
        await asyncio.sleep(1.0)  # the brain reloads its configuration (entry 898, the queue)

        session = await asyncio.to_thread(desk.start_session, user)
        check(session["desk_phone"] and session["endpoint"] == f"PJSIP/{DESK}", f"sign-in rings PJSIP/{DESK}")
        check(session["password"] is None, "no password is handed out for a desk phone")
        made = await asyncio.to_thread(SipAccount.objects.filter(sip_id=f"web{NUMBER}").exists)
        check(not made, "no browser phone was made")
        # Asterisk learns a new database phone's state at its next check (up to 30 s): ask like the page.
        check(
            await wait_for(lambda: desk.phone_state(user)["state"] == "online", seconds=40),
            "Asterisk reports the desk phone online (the page re-checks the same way)",
        )
        check(
            await wait_for(lambda: live_state(agent)["availability"] != "logged_out"),
            "the brain took the sign-in",
        )
        await asyncio.to_thread(desk.set_availability, user, ready=True)
        check(await wait_for(lambda: live_state(agent)["availability"] == "ready"), "the agent is Ready")

        started = timezone.now()
        out = await caller.dial(ENTRY)
        answered = await asyncio.wait_for(out.answered.wait(), timeout=20)
        check(answered, f"the caller dialled {ENTRY} and was answered")
        check(
            await wait_for(lambda: Event.objects.filter(
                type="call.offered", at__gte=started, agent_uid=str(agent.uid), data__endpoint=f"PJSIP/{DESK}"
            ).exists()),
            f"the brain offered the call on PJSIP/{DESK}",
        )
        check(
            await wait_for(lambda: Event.objects.filter(type="call.connected", at__gte=started).exists()),
            "the desk phone answered and the call connected",
        )
        await asyncio.sleep(2)
        await out.hangup()
        check(await wait_for(lambda: live_state(agent)["activity"] == "wrapup"), "the agent is in wrap-up")
        await asyncio.to_thread(desk.finish_wrapup, user, code="Information given", ready=True)
        await wait_for(lambda: live_state(agent)["activity"] != "wrapup")
        await asyncio.to_thread(desk.end_session, user)
        desk_account = await asyncio.to_thread(SipAccount.objects.get, sip_id=DESK)
        check(desk_account.active, "sign-out leaves the desk phone switched on")
    finally:
        await phone.close()
        await caller.close()


def main():
    print("Desk phone check (make dev's brain must be running):")
    try:
        user, agent, passwords = setup()
        asyncio.run(run(user, agent, passwords))
    except Exception as exc:  # noqa: BLE001 - report it, then clean up
        check(False, f"stopped: {exc!r}")
    finally:
        cleanup()
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)} of {len(results)} checks passed" + (": FAILED" if failed else "."))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
