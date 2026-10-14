"""Models of the teams app."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from apps.core.models import BaseModel


class Team(BaseModel):
    """Agents grouped under their supervisors. A supervisor sees and acts on their teams' calls."""

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    supervisors = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="supervised_teams", blank=True
    )
    agents = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="agent_teams", blank=True)

    class Meta:
        db_table = "teams_teams"
        ordering = ["name"]
        verbose_name = "team"
        verbose_name_plural = "teams"
        constraints = [
            models.UniqueConstraint(Lower("name"), condition=Q(deleted=False), name="uniq_live_team_name"),
        ]

    def __str__(self):
        return self.name
