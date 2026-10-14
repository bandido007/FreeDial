"""Keep the permission cache honest.

Any saved or deleted role, role permission or grant changes what somebody may
do, so each one bumps the cache version (see :mod:`apps.rbac.cache`).

``QuerySet.update()`` and ``bulk_create()`` do not send signals — the services
that use them call :func:`apps.rbac.cache.invalidate_all` themselves.
"""

from django.db.models.signals import post_delete, post_save

from apps.rbac import cache


def _invalidate(sender, **kwargs):
    cache.invalidate_all()


def connect() -> None:
    from apps.rbac.models import Permission, Role, RolePermission, UserRole

    for model in (Role, RolePermission, UserRole, Permission):
        label = model._meta.label_lower
        post_save.connect(_invalidate, sender=model, dispatch_uid=f"rbac-cache-save-{label}")
        post_delete.connect(_invalidate, sender=model, dispatch_uid=f"rbac-cache-delete-{label}")
