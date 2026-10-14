"""Agents: who the brain can offer calls to, and what they pick on the agent page.

An agent's number lives here, not on the user. A human agent signs in as a user;
an AI agent (M8) has no user. Where an agent takes calls is decided at login,
not stored here: the brain holds it with the agent's live state.
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
