"""Agents: who the brain can offer calls to, and what they pick on the agent page.

An agent's number lives here, not on the user. A human agent signs in as a user;
an AI agent (M8) has no user. Where an agent takes calls is decided at sign-in: the
desk phone an admin set on the agent (``phone``), or else the browser phone
web<number> made for that sign-in. The brain holds it with the agent's live state.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from apps.core.models import BaseModel


class Agent(BaseModel):
    number = models.CharField(max_length=20, help_text="The agent's number, e.g. 7001")
    name = models.CharField(max_length=120)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent",
        help_text="The person who signs in as this agent",
    )
    phone = models.ForeignKey(
        "telephony.SipAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agents",
        limit_choices_to={"kind": "SOFTPHONE", "deleted": False},
        help_text="Empty: calls ring the agent page in the browser. Or a desk phone or softphone "
        "(SIP account, e.g. 1001): calls ring that phone, and the agent answers and hangs up on it.",
    )

    class Meta:
        db_table = "agents_agents"
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=Q(deleted=False), name="uniq_live_agent_number"
            ),
        ]

    def __str__(self):
        return f"{self.number} {self.name}"


class NotReadyReason(BaseModel):
    """Why an agent is Not Ready (break, lunch, training…), picked on the agent page."""

    name = models.CharField(max_length=60)
    max_seconds = models.PositiveIntegerField(
        default=0, help_text="Show an attention message after this many seconds (0 = no limit)"
    )

    class Meta:
        db_table = "agents_not_ready_reasons"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"), condition=Q(deleted=False), name="uniq_live_not_ready_reason"
            ),
        ]

    def __str__(self):
        return self.name


class WrapupCode(BaseModel):
    """What came of a call (sale, complaint, callback…), picked when the agent wraps up."""

    name = models.CharField(max_length=60)

    class Meta:
        db_table = "agents_wrapup_codes"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), condition=Q(deleted=False), name="uniq_live_wrapup_code"),
        ]

    def __str__(self):
        return self.name


class AgentSession(models.Model):
    """One sign-in of an agent on the agent page: which phone, from when to when."""

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="sessions")
    sip_account = models.ForeignKey(
        "telephony.SipAccount", on_delete=models.SET_NULL, null=True, related_name="agent_sessions"
    )
    endpoint = models.CharField(
        max_length=80, help_text="Where the brain rings the agent, e.g. PJSIP/web7001"
    )
    login_at = models.DateTimeField(auto_now_add=True)
    logout_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agents_sessions"
        ordering = ["-login_at"]

    def __str__(self):
        return f"{self.agent} on {self.endpoint}"


class SkillKind(models.TextChoices):
    LANGUAGE = "language", "Language"
    TOPIC = "topic", "Topic"
    TIER = "tier", "Tier"


LEVEL_GUIDE = (
    "1-3 trainee (simple cases, with help) · 4-6 competent · 7-8 experienced (hard cases) · 9-10 expert"
)


class Skill(BaseModel):
    """Something an agent can do, at a level from 1 to 10: a language, a topic or a tier."""

    name = models.CharField(
        max_length=40, help_text="Lower-case, used in skill rules: e.g. swahili, claims, tier2"
    )
    kind = models.CharField(max_length=20, choices=SkillKind.choices, default=SkillKind.TOPIC)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "agents_skills"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name"], condition=Q(deleted=False), name="uniq_live_skill_name"),
        ]

    def __str__(self):
        return self.name


class AgentSkill(models.Model):
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="skills")
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="agents")
    level = models.PositiveSmallIntegerField(default=5, help_text=LEVEL_GUIDE)

    class Meta:
        db_table = "agents_agent_skills"
        ordering = ["skill__name"]
        constraints = [
            models.UniqueConstraint(fields=["agent", "skill"], name="uniq_agent_skill"),
            models.CheckConstraint(condition=Q(level__gte=1, level__lte=10), name="agent_skill_level_1_10"),
        ]

    def __str__(self):
        return f"{self.agent.number} {self.skill.name}={self.level}"
