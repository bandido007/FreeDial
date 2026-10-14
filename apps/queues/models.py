"""Queues and the entry points that lead to them.

M2 holds what the brain needs to hunt for an agent: ring timeout, max wait, retry
delay, hold music and the members. M4 adds strategies, skills and overflow targets.
"""

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from apps.agents.models import Agent
from apps.core.models import BaseModel


class Queue(BaseModel):
    name = models.CharField(max_length=120)
    ring_timeout = models.PositiveSmallIntegerField(default=20, help_text="Seconds an agent's phone rings")
    max_wait = models.PositiveIntegerField(default=300, help_text="Seconds a caller waits before overflow")
    retry_delay = models.PositiveSmallIntegerField(
        default=5, help_text="Seconds before agents who refused a call are offered it again"
    )
    moh_class = models.CharField(max_length=80, default="default", help_text="Asterisk music-on-hold class")
    agents = models.ManyToManyField(Agent, through="QueueMember", related_name="queues", blank=True)

    class Meta:
        db_table = "queues_queues"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), condition=Q(deleted=False), name="uniq_live_queue_name"),
        ]

    def __str__(self):
        return self.name


class QueueMember(models.Model):
    queue = models.ForeignKey(Queue, on_delete=models.CASCADE, related_name="members")
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="memberships")
    priority = models.PositiveSmallIntegerField(default=1, help_text="Used from M4: 1 is offered first")

    class Meta:
        db_table = "queues_members"
        constraints = [models.UniqueConstraint(fields=["queue", "agent"], name="uniq_queue_member")]

    def __str__(self):
        return f"{self.agent} in {self.queue}"


class EntryPoint(BaseModel):
    number = models.CharField(max_length=40, help_text="The number dialled, e.g. 800")
    name = models.CharField(max_length=120, blank=True)
    queue = models.ForeignKey(Queue, on_delete=models.PROTECT, related_name="entry_points")

    class Meta:
        db_table = "queues_entry_points"
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=Q(deleted=False), name="uniq_live_entry_number"
            ),
        ]

    def __str__(self):
        return self.number
