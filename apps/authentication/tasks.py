"""Celery tasks for the authentication domain."""

import logging

from celery import shared_task

from apps.authentication import password_services, tokens

logger = logging.getLogger(__name__)


@shared_task(name="authentication.purge_expired")
def purge_expired() -> dict:
    """Remove expired tokens and spent e-mailed links."""
    result = {
        "tokens": tokens.purge_expired_tokens(),
        "links": password_services.purge_spent_tokens(),
    }
    logger.info("[task:purge_expired] %s", result)
    return result
