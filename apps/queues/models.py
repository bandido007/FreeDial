"""Queues and the entry points that lead to them.

M2 holds what the brain needs to hunt for an agent: ring timeout, max wait, retry
delay, hold music and the members. M4 adds strategies, skills and overflow targets.
"""

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from apps.agents.models import Agent
from apps.core.models import BaseModel


class StrategyChoices(models.TextChoices):
    """Whom a queue rings (the brain's brain.core.config.Strategy; a test keeps them equal)."""

    LONGEST_IDLE = "longest_idle", "Longest idle agent"
    RING_ALL = "ring_all", "Ring all free agents, first to answer"
    ROUND_ROBIN = "round_robin", "Round robin (next after the last one rung)"
    LINEAR = "linear", "Linear (always from the first member)"
    FEWEST_CALLS = "fewest_calls", "Fewest calls answered"
    RANDOM = "random", "Random"


class Queue(BaseModel):
    name = models.CharField(max_length=120)
    ring_timeout = models.PositiveSmallIntegerField(default=20, help_text="Seconds an agent's phone rings")
    max_wait = models.PositiveIntegerField(default=300, help_text="Seconds a caller waits before overflow")
    retry_delay = models.PositiveSmallIntegerField(
        default=5, help_text="Seconds before agents who refused a call are offered it again"
    )
    moh_class = models.CharField(max_length=80, default="default", help_text="Asterisk music-on-hold class")
    wrapup_seconds = models.PositiveSmallIntegerField(
        default=60, help_text="After a call the agent has this long to wrap up (0 = no wrap-up)"
    )
    strategy = models.CharField(
        max_length=20, choices=StrategyChoices.choices, default=StrategyChoices.LONGEST_IDLE
    )
    max_callers = models.PositiveIntegerField(default=0, help_text="Callers waiting at most (0 = no limit)")
    priority = models.SmallIntegerField(
        default=0, help_text="A free agent in several queues takes the highest priority queue's caller first"
    )
    overflow_queue = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="overflow_from",
        help_text="Where a caller goes at max wait or max callers (empty = hang up)",
    )
    skill_rule_set = models.ForeignKey(
        "SkillRuleSet",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queues",
        help_text="Which agents may take a call, widening as the caller waits (empty = every member)",
    )
    service_level_seconds = models.PositiveSmallIntegerField(
        default=20, help_text="Target: answered within this many seconds…"
    )
    service_level_percent = models.PositiveSmallIntegerField(
        default=80, help_text="…for this percent of calls"
    )
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


class SkillRuleSet(BaseModel):
    """Rules that say which agents may take a call, widening as the caller waits."""

    name = models.CharField(max_length=120)

    class Meta:
        db_table = "queues_skill_rule_sets"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"), condition=Q(deleted=False), name="uniq_live_rule_set_name"
            ),
        ]

    def __str__(self):
        return self.name


class SkillRule(models.Model):
    """From ``after_seconds`` of waiting on, agents must match ``condition`` (e.g. ``swahili >= 7``)."""

    rule_set = models.ForeignKey(SkillRuleSet, on_delete=models.CASCADE, related_name="rules")
    after_seconds = models.PositiveIntegerField(default=0, help_text="Seconds of waiting in the queue")
    condition = models.CharField(
        max_length=300, help_text="e.g. swahili >= 7 & billing >= 5, or (english | swahili), or true"
    )

    class Meta:
        db_table = "queues_skill_rules"
        ordering = ["after_seconds"]
        constraints = [
            models.UniqueConstraint(fields=["rule_set", "after_seconds"], name="uniq_rule_second"),
        ]

    def __str__(self):
        return f"after {self.after_seconds}s: {self.condition}"
