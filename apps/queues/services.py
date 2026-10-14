"""Writes and rules of the queues app."""

import re

from django.db import transaction

from apps.core.exceptions import DuplicateError, NotFoundError, ValidationError
from apps.queues.models import EntryPoint, Queue, QueueMember

NOT_FOUND = 10300
NAME_TAKEN = 10301
NUMBER_TAKEN = 10302
OUT_OF_RANGE = 10303

LIMITS = {
    "ring_timeout": (5, 120),
    "max_wait": (10, 7200),
    "retry_delay": (0, 120),
    "wrapup_seconds": (0, 3600),
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
