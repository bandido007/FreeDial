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
    BEST_SKILLED = "best_skilled", "Best skilled (highest average level of the required skills)"
    RING_ALL = "ring_all", "Ring all free agents, first to answer"
    ROUND_ROBIN = "round_robin", "Round robin (next after the last one rung)"
    LINEAR = "linear", "Linear (always from the first member)"
    FEWEST_CALLS = "fewest_calls", "Fewest calls answered"
    RANDOM = "random", "Random"


class OverflowChoices(models.TextChoices):
    QUEUE = "queue", "Another queue"
    VOICEMAIL = "voicemail", "Voicemail, then a callback task"
    HANGUP = "hangup", "Hang up"


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
    overflow_action = models.CharField(
        max_length=20,
        choices=OverflowChoices.choices,
        default=OverflowChoices.HANGUP,
        help_text="What happens at max wait or max callers",
    )
    overflow_queue = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="overflow_from",
        help_text="For 'Another queue': which one",
    )
    skill_rule_set = models.ForeignKey(
        "SkillRuleSet",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queues",
        help_text="Which members may take a call, loosening as the caller waits (empty = every member)",
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
    language = models.ForeignKey(
        "agents.Skill",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        limit_choices_to={"kind": "language", "deleted": False},
        related_name="entry_points",
        help_text="The callers' language until something better is known (e.g. swahili)",
    )
    priority = models.SmallIntegerField(null=True, blank=True, help_text="Empty = the queue's priority")
    fallback_queue = models.ForeignKey(
        Queue,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fallback_for",
        help_text="Where the call goes if the AI or a routing decision fails",
    )
    ai_agent = models.ForeignKey(
        "ai.AiAgent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={"deleted": False},
        related_name="entry_points",
        help_text="An AI agent answers first and hands callers to the queue (M8); empty = straight to the queue",
    )

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


LANGUAGE = "$language"  # in a requirement: the call's language skill


class RuleRequirement(models.Model):
    """One minimum level in one step of a rule set (docs/ROUTING_DESIGN.md).

    Rows with the same ``until_seconds`` form a step: until that many seconds of waiting (empty:
    forever), an agent needs every row's skill at ``min_level`` or more. ``$language`` means the
    call's language, and every step must ask for it at 4 or more (the language floor).
    """

    rule_set = models.ForeignKey(SkillRuleSet, on_delete=models.CASCADE, related_name="requirements")
    until_seconds = models.PositiveIntegerField(
        null=True, blank=True, help_text="This step lasts until this many seconds of waiting; empty = forever"
    )
    skill = models.CharField(max_length=40, help_text="A skill name, or $language for the call's language")
    min_level = models.PositiveSmallIntegerField(help_text="1 to 10")

    class Meta:
        db_table = "queues_rule_requirements"
        ordering = [models.F("until_seconds").asc(nulls_last=True), "skill"]
        constraints = [
            models.UniqueConstraint(
                fields=["rule_set", "until_seconds", "skill"], nulls_distinct=False, name="uniq_step_skill"
            ),
            models.CheckConstraint(
                condition=Q(min_level__gte=1, min_level__lte=10), name="requirement_level_1_10"
            ),
        ]

    def __str__(self):
        until = f"< {self.until_seconds}s" if self.until_seconds is not None else "forever"
        return f"{until}: {self.skill} >= {self.min_level}"


class CarrierNumber(models.Model):
    """Every form a carrier may send for a DID (an entry point in E.164), and that DID. Asterisk's
    [from-carrier] reads it (REALTIME_FIELD), so only known DIDs reach the brain, even with the brain
    down. Rebuilt from the entry points and the trunks' number plans (``apps.queues.carriers``)."""

    form = models.CharField(max_length=40, unique=True, help_text="As the carrier sends it, e.g. 0222123456")
    did = models.CharField(max_length=40, help_text="The entry point's number, E.164")

    class Meta:
        db_table = "queues_carrier_numbers"
        ordering = ["did", "form"]

    def __str__(self):
        return f"{self.form} -> {self.did}"
