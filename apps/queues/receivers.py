"""Any change to a queue, its members, an entry point, a trunk or an outbound route tells the brain to
reload (rule 10). Entry points and trunks also rebuild the DID forms table [from-carrier] reads (M4b)."""

from django.db.models.signals import m2m_changed, post_delete, post_save

from apps.calls.bus import config_changed
from apps.queues import carriers
from apps.queues.models import EntryPoint, Queue, QueueMember, RuleRequirement, SkillRuleSet
from apps.telephony.models import OutboundRoute, RouteTrunk, Trunk


def _changed(sender, **kwargs):
    config_changed()


def _numbers_changed(sender, **kwargs):
    carriers.rebuild()
    config_changed()


def connect():
    for model in (Queue, QueueMember, SkillRuleSet, RuleRequirement, OutboundRoute, RouteTrunk):
        post_save.connect(_changed, sender=model, dispatch_uid=f"queues.{model.__name__}.changed")
        post_delete.connect(_changed, sender=model, dispatch_uid=f"queues.{model.__name__}.deleted")
    for model in (EntryPoint, Trunk):  # their numbers and number plans decide the DID forms
        post_save.connect(_numbers_changed, sender=model, dispatch_uid=f"queues.{model.__name__}.numbers")
        post_delete.connect(
            _numbers_changed, sender=model, dispatch_uid=f"queues.{model.__name__}.numbers_gone"
        )
    m2m_changed.connect(_changed, sender=Queue.agents.through, dispatch_uid="queues.members.changed")
