"""Celery tasks for roles and permissions."""

import logging

from celery import shared_task

from apps.rbac import services

logger = logging.getLogger(__name__)


@shared_task(name="rbac.retire_expired_grants")
def retire_expired_grants() -> int:
    """Retire role grants whose expiry date has passed."""
    return services.retire_expired_grants()
