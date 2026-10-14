"""Any change to a queue, its members or an entry point tells the brain to reload (rule 10)."""

from django.db.models.signals import m2m_changed, post_delete, post_save

from apps.calls.bus import config_changed
from apps.queues.models import EntryPoint, Queue, QueueMember, SkillRule, SkillRuleSet


def _changed(sender, **kwargs):
    config_changed()


def connect():
    for model in (Queue, QueueMember, EntryPoint, SkillRuleSet, SkillRule):
        post_save.connect(_changed, sender=model, dispatch_uid=f"queues.{model.__name__}.changed")
        post_delete.connect(_changed, sender=model, dispatch_uid=f"queues.{model.__name__}.deleted")
    m2m_changed.connect(_changed, sender=Queue.agents.through, dispatch_uid="queues.members.changed")
