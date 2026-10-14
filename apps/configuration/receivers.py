"""Forget the cached values whenever one changes, whatever changed it."""

from django.db.models.signals import post_delete, post_save


def _forget(sender, **kwargs):
    from apps.configuration import services

    services.forget()


def connect() -> None:
    from apps.configuration.models import SystemSetting

    post_save.connect(_forget, sender=SystemSetting, dispatch_uid="configuration-forget-on-save")
    post_delete.connect(_forget, sender=SystemSetting, dispatch_uid="configuration-forget-on-delete")
