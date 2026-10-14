"""Celery tasks for the users domain."""

import logging

from celery import shared_task

from apps.users import services

logger = logging.getLogger(__name__)


@shared_task(name="users.purge_stale_unverified_accounts")
def purge_stale_unverified_accounts() -> dict:
    """Purge abandoned never-verified signups + old code rows (frees email/phone)."""
    result = services.purge_stale_unverified_accounts()
    logger.info("[task:purge_stale_unverified_accounts] %s", result)
    return result
