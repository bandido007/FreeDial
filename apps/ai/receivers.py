"""A change to an AI agent tells the brain (and through it the worker's next call) to reload (rule 10)."""

from django.db.models.signals import post_delete, post_save

from apps.ai.models import AiAgent, AiRoute
from apps.calls.bus import config_changed


def _changed(sender, **kwargs):
    config_changed()


def connect():
    for model in (AiAgent, AiRoute):
        post_save.connect(_changed, sender=model, dispatch_uid=f"ai.{model.__name__}.changed")
        post_delete.connect(_changed, sender=model, dispatch_uid=f"ai.{model.__name__}.deleted")
