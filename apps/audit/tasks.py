"""Celery tasks — daily error-report e-mail + log retention."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="audit.send_daily_error_report")
def send_daily_error_report() -> dict:
    """E-mail yesterday's ERROR-level endpoint digest to subscribed users."""
    from apps.audit import services

    return services.send_error_report()


@shared_task(name="audit.purge_logs")
def purge_logs() -> dict:
    """Age out INFO/WARNING/resolved-ERROR request logs per the retention
    settings (unresolved errors are kept until someone resolves them), and the
    change trail when a retention period is configured."""
    from apps.audit import services

    purged = services.purge_request_logs()
    purged["AUDIT_TRAIL"] = services.purge_audit_trail()
    return purged
