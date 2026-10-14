"""Signal receivers: every save or delete of a SipAccount updates Asterisk's rows.

Hooked on the model rather than the services so the Django admin, a bulk
queryset delete or ``deactivate()`` cannot leave a stale endpoint behind.
"""

from django.db.models.signals import post_delete, post_save, pre_save

from apps.telephony import pjsip
from apps.telephony.models import SipAccount


def _remember_sip_id(sender, instance, **kwargs):
    old = SipAccount.all_objects.filter(pk=instance.pk).values_list("sip_id", flat=True).first()
    instance._previous_sip_id = old


def _sync(sender, instance, **kwargs):
    pjsip.sync(instance, previous_sip_id=getattr(instance, "_previous_sip_id", None))


def _remove(sender, instance, **kwargs):
    pjsip.remove(instance.sip_id)


def connect():
    pre_save.connect(_remember_sip_id, sender=SipAccount, dispatch_uid="telephony.sip.remember")
    post_save.connect(_sync, sender=SipAccount, dispatch_uid="telephony.sip.sync")
    post_delete.connect(_remove, sender=SipAccount, dispatch_uid="telephony.sip.remove")
