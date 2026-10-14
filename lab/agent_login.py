"""Make (or refresh) a lab agent you can sign in with on the agent page.

    cd ~/AlgoDial && .venv/bin/python lab/agent_login.py       (or: make agent-login)

The email and password live in .env (git-ignored): LAB_AGENT_EMAIL and LAB_AGENT_PASSWORD.
A missing password is generated and written there. The user gets the Agent role, is linked
to agent 7001 "Agent One" in queue Support, and Sale / Complaint / Break exist to pick from.
Running it again resets the password to the one in .env.
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

NUMBER, NAME = "7001", "Agent One"


def env_value(name):
    match = re.search(rf"^{name}=(.*)$", ENV.read_text(), re.MULTILINE)
    return match.group(1).strip() if match else ""


def set_env(name, value):
    text = ENV.read_text()
    if re.search(rf"^{name}=", text, re.MULTILINE):
        text = re.sub(rf"^{name}=.*$", f"{name}={value}", text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{name}={value}\n"
    ENV.write_text(text)


def main():
    if "# Lab agent for the agent page" not in ENV.read_text():
        ENV.write_text(ENV.read_text().rstrip("\n") + "\n\n# Lab agent for the agent page (lab/agent_login.py)\n")
    email = env_value("LAB_AGENT_EMAIL") or "agent1@example.org"
    password = env_value("LAB_AGENT_PASSWORD") or "Agent-" + secrets.token_urlsafe(9)
    set_env("LAB_AGENT_EMAIL", email)
    set_env("LAB_AGENT_PASSWORD", password)

    rbac.sync_catalogue()
    User = get_user_model()
    user = User.objects.filter(email=email).first()
    if user is None:
        user = User.objects.create_user(
            email=email, password=password, first_name="Agent", last_name="One",
            is_verified=True, account_type="Internal", phone="+255700007001",
        )
    else:
        user.set_password(password)
        user.is_active = True
        user.save()
    rbac.grant_role(user, rbac_selectors.get_role_by_name("Agent"))

    agent = Agent.objects.filter(number=NUMBER).first() or Agent.objects.create(number=NUMBER, name=NAME)
    if agent.user_id != user.pk:
        agent.user = user
        agent.save()

    support = Queue.objects.filter(name__iexact="Support").first()
    if support is None:
        support = queues.save_queue(actor=None, name="Support")
    members = list(support.agents.all())
    if agent not in members:
        queues.save_queue(actor=None, uid=support.uid, name=support.name, agents=[*members, agent])
    if not EntryPoint.objects.filter(number="800").exists():
        queues.save_entry_point(actor=None, number="800", queue=support, name="Lab entry")
    for name in ("Sale", "Complaint"):
        WrapupCode.objects.get_or_create(name=name)
    NotReadyReason.objects.get_or_create(name="Break")

    print(f"Agent page: http://127.0.0.1:8000/agent/")
    print(f"  email:    {email}")
    print(f"  password: {password}   (also in .env as LAB_AGENT_PASSWORD)")
    print(f"  agent {NUMBER} '{NAME}', queue {support.name}, entry point 800")


if __name__ == "__main__":
    main()
