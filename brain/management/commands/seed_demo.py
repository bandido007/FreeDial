import re
import secrets
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.agents import services as agents
from apps.agents.models import Agent, Skill
from apps.ai import demo as ai_demo
from apps.ai.models import AiAgent, AiRoute
from apps.queues import services as queues
from apps.queues.models import EntryPoint, Queue, SkillRuleSet
from apps.rbac import selectors as rbac_selectors
from apps.rbac import services as rbac
from apps.teams import services as teams
from apps.teams.models import Team
from brain.sim import demo


class Command(BaseCommand):
    help = (
        "Seed the demo contact centre of docs/ROUTING_DESIGN.md section 3: skills, agents and their users, "
        "teams, skill rule sets, queues and entry points 800-802. Safe to run again: it brings everything "
        "back to the spec."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            help="Password for every demo user. Default: DEMO_PASSWORD from .env (generated and saved there if empty).",
        )

    def handle(self, *args, **options):
        password = options["password"] or settings.DEMO_PASSWORD or self._new_password()
        with transaction.atomic():
            rbac.sync_catalogue()
            self._skills()
            users = self._users(password)
            self._agents(users)
            self._teams(users)
            self._rule_sets()
            self._queues()
            ai = self._ai_agent()
            self._entries(ai)
        self.stdout.write(self._summary())

    # ── each part ─────────────────────────────────────────────────────────
    def _skills(self):
        for name, kind in demo.SKILLS.items():
            skill = Skill.objects.filter(name=name).first() or agents.create_skill(
                actor=None, name=name, kind=kind
            )
            if skill.kind != kind:
                skill.kind = kind
                skill.save(update_fields=["kind"])

    def _users(self, password):
        User = get_user_model()
        people = [(name, number) for name, number, *_ in demo.AGENTS]
        people += [
            (supervisor, f"79{index:02d}") for index, supervisor in enumerate(demo.TEAMS.values(), start=1)
        ]
        users = {}
        for name, number in people:
            email = f"{name.lower()}@example.org"
            user = User.objects.filter(email=email).first()
            if user is None:
                user = User.objects.create_user(
                    email=email,
                    password=password,
                    first_name=name,
                    last_name="Demo",
                    is_verified=True,
                    account_type="Internal",
                    phone=f"+2557110{number}",
                )
            else:
                user.set_password(password)
                user.is_active = True
                user.save()
            users[name] = user
        agent_role = rbac_selectors.get_role_by_name("Agent")
        for name, *_ in demo.AGENTS:
            rbac.grant_role(users[name], agent_role)
        supervisor_role = rbac_selectors.get_role_by_name("Supervisor")
        for supervisor in demo.TEAMS.values():
            rbac.grant_role(users[supervisor], supervisor_role)
        return users

    def _agents(self, users):
        for name, number, _team, levels, _queues in demo.AGENTS:
            agent = Agent.objects.filter(number=number).first()
            if agent is None:
                agent = agents.create_agent(actor=None, number=number, name=name, user=users[name])
            elif (agent.name, agent.user_id) != (name, users[name].pk):
                agents.update_agent(uid=agent.uid, actor=None, name=name, user=users[name])
            agents.set_agent_skills(agent=agent, levels=levels)

    def _teams(self, users):
        for team_name, supervisor in demo.TEAMS.items():
            members = [users[name] for name, _n, team, *_ in demo.AGENTS if team == team_name]
            team = Team.objects.filter(name=team_name).first()
            if team is None:
                teams.create_team(actor=None, name=team_name, supervisors=[users[supervisor]], agents=members)
            else:
                teams.update_team(uid=team.uid, actor=None, supervisors=[users[supervisor]], agents=members)

    def _rule_sets(self):
        for name, steps in demo.RULE_SETS.items():
            existing = SkillRuleSet.objects.filter(name=name).first()
            queues.save_skill_rule_set(
                actor=None, name=name, steps=steps, uid=existing.uid if existing else None
            )

    def _queues(self):
        rule_sets = {rule_set.name: rule_set for rule_set in SkillRuleSet.objects.all()}
        by_number = {agent.number: agent for agent in Agent.objects.all()}
        saved = {}
        for name, settings_ in demo.QUEUES.items():
            existing = Queue.objects.filter(name__iexact=name).first()
            members = [
                by_number[number] for agent, number, _t, _l, member_of in demo.AGENTS if name in member_of
            ]
            seconds, percent = settings_["service_level"]
            saved[name] = queues.save_queue(
                actor=None,
                uid=existing.uid if existing else None,
                name=name,
                agents=members,
                strategy=settings_["strategy"],
                priority=settings_["priority"],
                ring_timeout=settings_["ring_timeout"],
                max_wait=settings_["max_wait"],
                wrapup_seconds=0,
                service_level_seconds=seconds,
                service_level_percent=percent,
                skill_rule_set=rule_sets[settings_["rule_set"]],
                overflow_action=settings_["overflow"][0],
                overflow_queue=None,
            )
        for name, settings_ in demo.QUEUES.items():  # overflow targets once every queue exists
            target = settings_["overflow"][1]
            queue = saved[name]
            queue.overflow_queue = saved[target] if target else None
            queue.save()

    def _ai_agent(self) -> AiAgent:
        """Msaidizi, the demo AI agent, with the queues it may send callers to."""
        ai = AiAgent.objects.filter(name=ai_demo.NAME, deleted=False).first() or AiAgent(name=ai_demo.NAME)
        ai.instructions = ai_demo.INSTRUCTIONS
        ai.greeting_swahili, ai.greeting_english = ai_demo.GREETING_SWAHILI, ai_demo.GREETING_ENGLISH
        ai.sensitive_topics = "\n".join(ai_demo.SENSITIVE_TOPICS)
        ai.active = True
        ai.save()
        for position, (queue, when, skill, level) in enumerate(ai_demo.ROUTES):
            AiRoute.objects.update_or_create(
                ai_agent=ai,
                queue=Queue.objects.get(name__iexact=queue, deleted=False),
                defaults={
                    "when": when,
                    "skill": Skill.objects.filter(name=skill, deleted=False).first() if skill else None,
                    "min_level": level,
                    "position": position,
                },
            )
        return ai

    def _entries(self, ai: AiAgent):
        for number, queue, language, priority, fallback in demo.ENTRIES:
            values = {
                "queue": Queue.objects.get(name__iexact=queue),
                "language": Skill.objects.get(name=language),
                "priority": priority,
                "fallback_queue": Queue.objects.get(name__iexact=fallback),
                "name": f"Demo {queue}",
                "ai_agent": ai if number in ai_demo.ENTRY_NUMBERS else None,
            }
            entry = EntryPoint.objects.filter(number=number).first()
            if entry is None:
                queues.save_entry_point(actor=None, number=number, queue=values["queue"], name=values["name"])
                entry = EntryPoint.objects.get(number=number)
            for field, value in values.items():
                setattr(entry, field, value)
            entry.save()

    # ── output ────────────────────────────────────────────────────────────
    def _new_password(self):
        password = "Demo-" + secrets.token_urlsafe(9)
        env = Path(settings.BASE_DIR) / ".env"
        text = env.read_text() if env.exists() else ""
        line = f"DEMO_PASSWORD={password}"
        text = (
            re.sub(r"^DEMO_PASSWORD=.*$", line, text, flags=re.MULTILINE)
            if "DEMO_PASSWORD=" in text
            else (text.rstrip("\n") + f"\n# Demo centre users (manage.py seed_demo)\n{line}\n")
        )
        env.write_text(text)
        settings.DEMO_PASSWORD = password
        return password

    def _summary(self):
        lines = ["Demo centre seeded (docs/ROUTING_DESIGN.md section 3):"]
        for name, number, team, _levels, member_of in demo.AGENTS:
            lines.append(
                f"  {number} {name:<7} {name.lower()}@example.org  {team:<19} {', '.join(member_of)}"
            )
        lines.append(
            f"  supervisors (Supervisor role, /supervisor/): {', '.join(f'{s.lower()}@example.org' for s in demo.TEAMS.values())}"
        )
        lines.append(
            "  entry points: 800 General (swahili), 801 Providers (swahili, priority 10), 802 General (english)"
        )
        lines.append(
            f"  AI agent {ai_demo.NAME} answers 800 and 802 first (needs `manage.py run_ai`; without it, "
            "callers reach General after 10 s)"
        )
        lines.append("  every demo user's password is DEMO_PASSWORD in .env")
        return "\n".join(lines)
