"""Writes and rules of the agents app."""

import re

from django.db import transaction

from apps.agents.models import Agent, AgentSkill, Skill, SkillKind
from apps.core.exceptions import DuplicateError, NotFoundError, ValidationError
from apps.core.hooks import policy

NOT_FOUND = 10200
NUMBER_TAKEN = 10201
BAD_NUMBER = 10202
USER_TAKEN = 10203
NOT_A_DESK_PHONE = 10210

NUMBER_PATTERN = re.compile(r"^\d{2,20}$")
SKILL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def get_agent(uid) -> Agent:
    agent = Agent.objects.filter(uid=uid).first()
    if agent is None:
        raise NotFoundError("Agent not found.", code=NOT_FOUND)
    return agent


def validate_agent(*, number, name, user=None, except_pk=None) -> tuple[str, str]:
    number = (number or "").strip()
    name = " ".join((name or "").split())
    if not NUMBER_PATTERN.match(number):
        raise ValidationError("An agent number is 2 to 20 digits.", code=BAD_NUMBER)
    if not name:
        raise ValidationError("An agent needs a name.", code=9005)
    if Agent.objects.filter(number=number).exclude(pk=except_pk).exists():
        raise DuplicateError(f"Agent {number} already exists.", code=NUMBER_TAKEN)
    if user is not None and Agent.all_objects.filter(user=user).exclude(pk=except_pk).exists():
        raise DuplicateError("This user is already another agent.", code=USER_TAKEN)
    return number, name


def number_range() -> tuple[int, int]:
    """The agent numbers the Add agent form suggests from (System settings)."""
    return int(policy("AGENT_NUMBER_FIRST", 2000)), int(policy("AGENT_NUMBER_LAST", 6999))


def next_free_number() -> str | None:
    """The lowest number of the range no agent has ever had, or None when the range is full.

    Deleted agents' numbers are skipped too: a new agent with an old number would take over the old
    agent's browser phone (web<number>) and its sign-in history."""
    first, last = number_range()
    used = {int(n) for n in Agent.all_objects.values_list("number", flat=True) if n.isdigit()}
    return next((str(n) for n in range(first, last + 1) if n not in used), None)


def validate_phone(phone) -> None:
    """An agent's own phone is a desk phone or softphone: browser accounts are made at each sign-in."""
    if phone is not None and (phone.kind != "SOFTPHONE" or phone.deleted):
        raise ValidationError(
            "An agent's phone is a desk phone or softphone (a SOFTPHONE SIP account). Leave it empty "
            "for the browser phone.",
            code=NOT_A_DESK_PHONE,
        )


@transaction.atomic
def create_agent(*, actor, number, name, user=None, phone=None) -> Agent:
    number, name = validate_agent(number=number, name=name, user=user)
    validate_phone(phone)
    return Agent.objects.create(number=number, name=name, user=user, phone=phone, created_by=actor)


@transaction.atomic
def update_agent(
    *, uid, actor, number=None, name=None, user=None, clear_user=False, phone=None, clear_phone=False
) -> Agent:
    agent = get_agent(uid)
    new_user = None if clear_user else (user or agent.user)
    agent.number, agent.name = validate_agent(
        number=agent.number if number is None else number,
        name=agent.name if name is None else name,
        user=new_user,
        except_pk=agent.pk,
    )
    new_phone = None if clear_phone else (phone or agent.phone)
    validate_phone(new_phone)
    agent.user, agent.phone = new_user, new_phone
    agent.updated_by = actor
    agent.save()
    return agent


@transaction.atomic
def delete_agent(*, uid) -> Agent:
    agent = get_agent(uid)
    agent.user = None  # the user may become another agent later
    agent.save(update_fields=["user"])
    agent.delete()  # soft delete; the brain logs the agent out once any call ends
    return agent


# ── skills ─────────────────────────────────────────────────────────────────
BAD_SKILL = 10208
SKILL_TAKEN = 10209


def validate_skill_name(name, *, except_pk=None) -> str:
    name = (name or "").strip().lower()
    if not SKILL_NAME.match(name):
        raise ValidationError(
            "A skill name is lower-case letters, digits and _, starting with a letter (e.g. swahili).",
            code=BAD_SKILL,
        )
    if Skill.objects.filter(name=name).exclude(pk=except_pk).exists():
        raise DuplicateError(f"The skill {name} already exists.", code=SKILL_TAKEN)
    return name


@transaction.atomic
def create_skill(*, actor, name, kind=SkillKind.TOPIC, description="") -> Skill:
    if kind not in SkillKind.values:
        raise ValidationError(f"A skill is a {', '.join(SkillKind.values)}.", code=BAD_SKILL)
    return Skill.objects.create(
        name=validate_skill_name(name), kind=kind, description=description.strip(), created_by=actor
    )


@transaction.atomic
def set_agent_skills(*, agent, levels: dict) -> None:
    """Replace the agent's skills: ``{skill_name: level}``, levels 1 to 10."""
    skills = {skill.name: skill for skill in Skill.objects.filter(name__in=levels)}
    unknown = set(levels) - set(skills)
    if unknown:
        raise ValidationError(f"Unknown skill(s): {', '.join(sorted(unknown))}.", code=BAD_SKILL)
    if any(not 1 <= int(level) <= 10 for level in levels.values()):
        raise ValidationError("A skill level is 1 to 10.", code=BAD_SKILL)
    AgentSkill.objects.filter(agent=agent).exclude(skill__name__in=levels).delete()
    for name, level in levels.items():
        AgentSkill.objects.update_or_create(agent=agent, skill=skills[name], defaults={"level": int(level)})
