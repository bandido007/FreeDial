"""Writes and rules of the queues app."""

import re

from django.db import transaction

from apps.core.exceptions import DuplicateError, NotFoundError, ValidationError
from apps.queues.models import LANGUAGE, EntryPoint, Queue, QueueMember, RuleRequirement, SkillRuleSet

NOT_FOUND = 10300
NAME_TAKEN = 10301
NUMBER_TAKEN = 10302
OUT_OF_RANGE = 10303
BAD_CONDITION = 10304
BAD_OVERFLOW = 10305

LIMITS = {
    "ring_timeout": (5, 120),
    "max_wait": (10, 7200),
    "retry_delay": (0, 120),
    "wrapup_seconds": (0, 3600),
    "max_callers": (0, 10000),
    "priority": (-100, 100),
    "service_level_seconds": (1, 3600),
    "service_level_percent": (1, 100),
}
ENTRY_PATTERN = re.compile(r"^[0-9*#+]{2,40}$")


def get_queue(uid) -> Queue:
    queue = Queue.objects.filter(uid=uid).first()
    if queue is None:
        raise NotFoundError("Queue not found.", code=NOT_FOUND)
    return queue


def validate_queue(*, name, except_pk=None, **settings) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ValidationError("A queue needs a name.", code=9005)
    if Queue.objects.filter(name__iexact=name).exclude(pk=except_pk).exists():
        raise DuplicateError(f"A queue called {name} already exists.", code=NAME_TAKEN)
    for field, (low, high) in LIMITS.items():
        value = settings.get(field)
        if value is not None and not low <= int(value) <= high:
            raise ValidationError(f"{field} must be between {low} and {high} seconds.", code=OUT_OF_RANGE)
    return name


@transaction.atomic
def save_queue(*, actor, name, uid=None, agents=None, **settings) -> Queue:
    """Create (no ``uid``) or change a queue. ``agents``, when given, replace its members."""
    queue = get_queue(uid) if uid else Queue(created_by=actor)
    queue.name = validate_queue(name=name, except_pk=queue.pk, **settings)
    for field, value in settings.items():
        setattr(queue, field, value)
    queue.updated_by = actor
    queue.save()
    if agents is not None:
        keep = {agent.pk for agent in agents}
        queue.members.exclude(agent_id__in=keep).delete()
        have = set(queue.members.values_list("agent_id", flat=True))
        QueueMember.objects.bulk_create(
            [QueueMember(queue=queue, agent=agent) for agent in agents if agent.pk not in have]
        )
    return queue


@transaction.atomic
def delete_queue(*, uid) -> Queue:
    queue = get_queue(uid)
    for entry in queue.entry_points.all():
        entry.delete()
    queue.delete()
    return queue


def validate_entry_point(*, number, except_pk=None) -> str:
    number = (number or "").strip()
    if not ENTRY_PATTERN.match(number):
        raise ValidationError("An entry point number is 2 to 40 of 0-9 * # +.", code=9005)
    if EntryPoint.objects.filter(number=number).exclude(pk=except_pk).exists():
        raise DuplicateError(f"{number} already leads somewhere.", code=NUMBER_TAKEN)
    return number


@transaction.atomic
def save_entry_point(*, actor, number, queue, name="", uid=None) -> EntryPoint:
    entry = EntryPoint.objects.filter(uid=uid).first() if uid else EntryPoint(created_by=actor)
    if entry is None:
        raise NotFoundError("Entry point not found.", code=NOT_FOUND)
    entry.number = validate_entry_point(number=number, except_pk=entry.pk)
    entry.name, entry.queue, entry.updated_by = (name or "").strip(), queue, actor
    entry.save()
    return entry


def validate_overflow(queue, overflow_queue) -> None:
    if overflow_queue is not None and queue is not None and overflow_queue.pk == queue.pk:
        raise ValidationError("A queue cannot overflow into itself.", code=BAD_OVERFLOW)


LANGUAGE_FLOOR = 4  # every step asks for the call's language at this level or more


def validate_steps(steps) -> list[tuple[int | None, dict[str, int]]]:
    """Steps as [(until_seconds or None, {skill or $language: min level})], checked against ROUTING_DESIGN.md.

    - every step asks for $language at 4 or more (the language floor: language is never dropped);
    - steps run in time order and the last one, only the last, lasts forever;
    - skills exist and levels are 1 to 10.
    """
    from apps.agents.models import Skill

    if not steps:
        raise ValidationError("A rule set needs at least one step.", code=BAD_CONDITION)
    known = set(Skill.objects.values_list("name", flat=True))
    clean: list[tuple[int | None, dict[str, int]]] = []
    for position, (until, needs) in enumerate(steps):
        last = position == len(steps) - 1
        if (until is None) != last:
            raise ValidationError(
                "Only the last step lasts forever (empty seconds), and it must.", code=BAD_CONDITION
            )
        if until is not None and (
            int(until) <= 0 or (clean and clean[-1][0] is not None and int(until) <= clean[-1][0])
        ):
            raise ValidationError("Steps end at increasing numbers of seconds.", code=BAD_CONDITION)
        needs = {str(skill).strip().lower(): int(level) for skill, level in needs.items()}
        if needs.get(LANGUAGE, 0) < LANGUAGE_FLOOR:
            raise ValidationError(
                f"Every step must ask for the call's language ($language) at {LANGUAGE_FLOOR} or more.",
                code=BAD_CONDITION,
            )
        unknown = sorted(skill for skill in needs if skill != LANGUAGE and skill not in known)
        if unknown:
            raise ValidationError(f"Unknown skill(s): {', '.join(unknown)}.", code=BAD_CONDITION)
        if any(not 1 <= level <= 10 for level in needs.values()):
            raise ValidationError("A minimum level is 1 to 10.", code=BAD_CONDITION)
        clean.append((None if until is None else int(until), needs))
    return clean


def rule_steps(rule_set) -> list[tuple[int | None, dict[str, int]]]:
    """A rule set's steps in time order, forever last."""
    steps: dict[int | None, dict[str, int]] = {}
    for row in rule_set.requirements.all():
        steps.setdefault(row.until_seconds, {})[row.skill] = row.min_level
    return sorted(steps.items(), key=lambda step: (step[0] is None, step[0] or 0))


@transaction.atomic
def save_skill_rule_set(*, actor, name, steps, uid=None) -> SkillRuleSet:
    """Create or change a rule set; ``steps`` replace its current ones (see :func:`validate_steps`)."""
    rule_set = SkillRuleSet.objects.filter(uid=uid).first() if uid else SkillRuleSet(created_by=actor)
    if rule_set is None:
        raise NotFoundError("Skill rule set not found.", code=NOT_FOUND)
    rule_set.name = " ".join((name or "").split())
    if not rule_set.name:
        raise ValidationError("A skill rule set needs a name.", code=9005)
    clean = validate_steps(steps)
    rule_set.updated_by = actor
    rule_set.save()
    rule_set.requirements.all().delete()
    RuleRequirement.objects.bulk_create(
        [
            RuleRequirement(rule_set=rule_set, until_seconds=until, skill=skill, min_level=level)
            for until, needs in clean
            for skill, level in needs.items()
        ]
    )
    return rule_set
