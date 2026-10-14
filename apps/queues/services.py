"""Writes and rules of the queues app."""

import re

from django.db import transaction

from apps.core.exceptions import DuplicateError, NotFoundError, ValidationError
from apps.queues.models import EntryPoint, Queue, QueueMember, SkillRule, SkillRuleSet
from contracts import skills as skill_language

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


def validate_condition(text) -> str:
    """A skill condition the brain can evaluate, naming only skills that exist."""
    from apps.agents.models import Skill

    text = " ".join((text or "").split())
    try:
        parsed = skill_language.parse(text)
    except skill_language.SkillConditionError as exc:
        raise ValidationError(f"The condition is not valid: {exc}.", code=BAD_CONDITION) from exc
    unknown = skill_language.skill_names(parsed) - set(Skill.objects.values_list("name", flat=True))
    if unknown:
        raise ValidationError(
            f"Unknown skill(s) in the condition: {', '.join(sorted(unknown))}.", code=BAD_CONDITION
        )
    return text


@transaction.atomic
def save_skill_rule_set(*, actor, name, rules, uid=None) -> SkillRuleSet:
    """Create or change a rule set. ``rules`` = [(after_seconds, condition)], replacing the current rules."""
    rule_set = SkillRuleSet.objects.filter(uid=uid).first() if uid else SkillRuleSet(created_by=actor)
    if rule_set is None:
        raise NotFoundError("Skill rule set not found.", code=NOT_FOUND)
    rule_set.name = " ".join((name or "").split())
    if not rule_set.name:
        raise ValidationError("A skill rule set needs a name.", code=9005)
    seconds = [int(after) for after, _ in rules]
    if len(set(seconds)) != len(seconds) or any(after < 0 for after in seconds):
        raise ValidationError("Each rule needs its own, non-negative number of seconds.", code=BAD_CONDITION)
    checked = [(int(after), validate_condition(condition)) for after, condition in rules]
    rule_set.updated_by = actor
    rule_set.save()
    rule_set.rules.all().delete()
    SkillRule.objects.bulk_create(
        [SkillRule(rule_set=rule_set, after_seconds=a, condition=c) for a, c in checked]
    )
    return rule_set
