"""Any change to an agent or its skills tells the brain to reload its configuration (rule 10)."""

from django.db.models.signals import post_delete, post_save

from apps.agents.models import Agent, AgentSkill, Skill
from apps.calls.bus import config_changed


def _changed(sender, instance, **kwargs):
    config_changed()


def connect():
    for model in (Agent, Skill, AgentSkill):
        post_save.connect(_changed, sender=model, dispatch_uid=f"agents.{model.__name__}.changed")
        post_delete.connect(_changed, sender=model, dispatch_uid=f"agents.{model.__name__}.deleted")
