"""Make (or refresh) the lab agents you can sign in with on the agent page, and their pick lists.

    cd ~/AlgoDial && .venv/bin/python lab/agent_login.py       (or: make agent-login)

Emails and passwords live in .env (git-ignored); a missing password is generated and
written there, and running this again resets each user to the password in .env.

  agent 7001 "Agent One"          LAB_AGENT_EMAIL / LAB_AGENT_PASSWORD               for you
  agent 9003 "Lab browser agent"  LAB_CHECK_AGENT_EMAIL / LAB_CHECK_AGENT_PASSWORD   make agentcheck uses it

Both have the Agent role and sit in queue Support (lab entry point 899). A lab supervisor
(LAB_SUPERVISOR_EMAIL / LAB_SUPERVISOR_PASSWORD) supervises them as team "Lab".
"""
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402

from apps.agents.models import Agent, NotReadyReason, WrapupCode  # noqa: E402
from apps.queues import services as queues  # noqa: E402
from apps.queues.models import EntryPoint, Queue  # noqa: E402
from apps.rbac import selectors as rbac_selectors  # noqa: E402
from apps.rbac import services as rbac  # noqa: E402

# (env prefix, default email, agent number, agent name, first name, last name, phone)
AGENTS = [
    ("LAB_AGENT", "agent1@example.org", "7001", "Agent One", "Agent", "One", "+255700007001"),
    ("LAB_CHECK_AGENT", "lab.agent@example.org", "9003", "Lab browser agent", "Lab", "Agent", "+255700000903"),
]

WRAPUP_CODES = [
    "Sale",
    "Complaint",
    "Information given",
    "Callback requested",
    "Payment issue",
    "Technical support",
    "Escalated to supervisor",
    "Transferred",
    "Wrong number",
    "Silent call",
    "Customer hung up",
]
NOT_READY_REASONS = ["Break", "Lunch", "Meeting", "Training", "After-call paperwork", "Technical problem"]


def env_value(name):
    match = re.search(rf"^{name}=(.*)$", ENV.read_text(), re.MULTILINE)
    return match.group(1).strip() if match else ""


def set_env(name, value):
    text = ENV.read_text()
    if re.search(rf"^{name}=", text, re.MULTILINE):
        text = re.sub(rf"^{name}=.*$", lambda _: f"{name}={value}", text, flags=re.MULTILINE)
    else:
        if "# Lab agents for the agent page" not in text:
            text = text.rstrip("\n") + "\n\n# Lab agents for the agent page (lab/agent_login.py)"
        text = text.rstrip("\n") + f"\n{name}={value}\n"
    ENV.write_text(text)


def support_queue():
    support = Queue.objects.filter(name__iexact="Support").first()
    if support is None:
        support = queues.save_queue(actor=None, name="Support")
    if not EntryPoint.objects.filter(number="800").exists():
        queues.save_entry_point(actor=None, number="800", queue=support, name="Lab entry")
    return support


def ensure_agent(prefix, default_email, number, name, first, last, phone):
    """The user and agent behind one lab login. Returns (email, password, agent)."""
    email = env_value(f"{prefix}_EMAIL") or default_email
    password = env_value(f"{prefix}_PASSWORD") or "Agent-" + secrets.token_urlsafe(9)
    set_env(f"{prefix}_EMAIL", email)
    set_env(f"{prefix}_PASSWORD", password)

    User = get_user_model()
    user = User.objects.filter(email=email).first()
    if user is None:
        user = User.objects.create_user(
            email=email, password=password, first_name=first, last_name=last,
            is_verified=True, account_type="Internal", phone=phone,
        )
    else:
        user.set_password(password)
        user.is_active = True
        user.save()
    rbac.grant_role(user, rbac_selectors.get_role_by_name("Agent"))  # idempotent

    agent = Agent.objects.filter(number=number).first() or Agent.objects.create(number=number, name=name)
    if agent.user_id != user.pk:
        agent.user = user
        agent.save()
    support = support_queue()
    members = list(support.agents.all())
    if agent not in members:
        queues.save_queue(actor=None, uid=support.uid, name=support.name, agents=[*members, agent])
    return email, password, agent


def ensure_supervisor():
    """A lab supervisor (LAB_SUPERVISOR_* in .env) of team "Lab": the two lab agents. Returns (email, password)."""
    from apps.teams import services as teams
    from apps.teams.models import Team

    email = env_value("LAB_SUPERVISOR_EMAIL") or "lab.supervisor@example.org"
    password = env_value("LAB_SUPERVISOR_PASSWORD") or "Sup-" + secrets.token_urlsafe(9)
    set_env("LAB_SUPERVISOR_EMAIL", email)
    set_env("LAB_SUPERVISOR_PASSWORD", password)
    User = get_user_model()
    user = User.objects.filter(email=email).first()
    if user is None:
        user = User.objects.create_user(
            email=email, password=password, first_name="Lab", last_name="Supervisor",
            is_verified=True, account_type="Internal", phone="+255700009901",
        )
    else:
        user.set_password(password)
        user.is_active = True
        user.save()
    rbac.grant_role(user, rbac_selectors.get_role_by_name("Supervisor"))
    agent_users = [agent.user for agent in Agent.objects.filter(number__in=["7001", "9003"]) if agent.user]
    team = Team.objects.filter(name="Lab").first()
    if team is None:
        teams.create_team(actor=None, name="Lab", supervisors=[user], agents=agent_users)
    else:
        teams.update_team(uid=team.uid, actor=None, supervisors=[user], agents=agent_users)
    return email, password


def ensure_pick_lists():
    for name in WRAPUP_CODES:
        WrapupCode.objects.get_or_create(name=name)
    for name in NOT_READY_REASONS:
        NotReadyReason.objects.get_or_create(name=name)


def main():
    rbac.sync_catalogue()
    ensure_pick_lists()
    print("Agent page: http://127.0.0.1:8000/agent/  (queue Support, entry point 800)")
    for spec in AGENTS:
        email, password, agent = ensure_agent(*spec)
        print(f"  agent {agent.number} {agent.name!r:22} {email:26} password {password}")
    email, password = ensure_supervisor()
    print(f"Supervisor page: http://127.0.0.1:8000/supervisor/  (team Lab: agents 7001, 9003)")
    print(f"  {email:26} password {password}")
    print(f"Wrap-up codes: {', '.join(WRAPUP_CODES)}")
    print(f"Not Ready reasons: {', '.join(NOT_READY_REASONS)}")


if __name__ == "__main__":
    main()
