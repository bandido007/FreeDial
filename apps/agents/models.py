"""Agents: who the brain can offer calls to.

An agent's number lives here, not on the user. A human agent signs in as a user;
an AI agent (M8) has no user. Where an agent takes calls is decided at login,
not stored here: the brain holds it with the agent's live state.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

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
