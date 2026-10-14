"""Any change to an agent tells the brain to reload its configuration (rule 10)."""

from django.db.models.signals import post_delete, post_save

from apps.agents.models import Agent
from apps.calls.bus import config_changed


def _changed(sender, instance, **kwargs):
    config_changed()


def connect():
    post_save.connect(_changed, sender=Agent, dispatch_uid="agents.agent.changed")
    post_delete.connect(_changed, sender=Agent, dispatch_uid="agents.agent.deleted")
