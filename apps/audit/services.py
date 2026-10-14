"""Audit write logic.

Two families of writers live here:

* **Recorders** (``record_audit`` / ``record_request_log``) — called from the
  signal receivers and the middleware. They swallow their own failures: an
  observability write must never break the operation it observes, so they are
  the one deliberate exception to the raise-on-failure service convention.
* **Domain writers** (resolution workflow, error-report recipients, the daily
  report itself, retention purge) — normal services: keyword-only args,
  ``@transaction.atomic``, raise :class:`~apps.core.exceptions.ApplicationError`
  subclasses on failure.
"""

import logging
from collections.abc import Callable
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Max
from django.utils import timezone

from apps.audit import context as audit_context
from apps.audit import signals
from apps.audit.enums import AuditActionChoices, LogLevelChoices
from apps.audit.models import AuditLog, ErrorReportSubscription, RequestLog
from apps.audit.notifications import ERROR_DIGEST
from apps.core.context import get_current_user, get_request_id
from apps.core.exceptions import NotFoundError, ValidationError
from apps.notifications import routes
from apps.notifications import services as notification_services
from apps.notifications.enums import NotificationChannelChoices

logger = logging.getLogger(__name__)

NOT_AN_ERROR = 9220
ALREADY_RESOLVED = 9221
NOT_RESOLVED = 9222
BULK_AUDIT_LIMIT = 5000


# ── Recorders (best-effort, never raise) ─────────────────────────────────────
def _audit_row(*, instance, action: str, changes: dict) -> AuditLog:
    user = get_current_user()
    actor = user if user is not None and getattr(user, "is_authenticated", False) else None
    try:
        object_repr = str(instance)[:255]
    except Exception:  # noqa: BLE001 — __str__ may touch deleted relations
        object_repr = ""
    ctx = audit_context.get_audit_context()
    return AuditLog(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        actor_name=(actor.get_full_name() if actor else "") or "",
        action=action,
        entity=instance._meta.label,
        app_label=instance._meta.app_label,
        model_name=instance._meta.model_name,
        object_uid=getattr(instance, "uid", None),
        object_pk=str(instance.pk) if instance.pk is not None else "",
        object_repr=object_repr,
        changes=changes,
        request_id=(ctx.request_id if ctx else get_request_id()) or "",
        ip_address=ctx.ip_address if ctx else None,
        user_agent=(ctx.user_agent if ctx else "")[:1000],
    )


def record_audit(*, instance, action: str, changes: dict) -> AuditLog | None:
    """Persist one model-change row. Actor and request details are pulled from
    the request context so call sites (signal receivers) stay one-liners."""
    try:
        row = _audit_row(instance=instance, action=action, changes=changes)
        row.save()
        return row
    except Exception:  # noqa: BLE001
        logger.warning("[record_audit] failed for %s", type(instance).__name__, exc_info=True)
        return None


def record_request_log(**fields) -> RequestLog | None:
    """Persist one endpoint-transaction row (called by the middleware)."""
    try:
        user = fields.get("user")
        fields["user_email"] = getattr(user, "email", "") or ""
        return RequestLog.objects.create(**fields)
    except Exception:  # noqa: BLE001
        logger.warning("[record_request_log] failed", exc_info=True)
        return None


@transaction.atomic
def audited_update(queryset, **values) -> int:
    """``queryset.update(**values)`` that leaves a trail.

    A plain ``update()`` sends no signals, so its changes never reach the
    change trail. This reads the affected rows first, applies the update, and
    writes one audit row per record that actually changed::

        audited_update(Invoice.objects.filter(batch=batch), status="PAID")

    Meant for administrative bulk actions, not for high-volume jobs: it refuses
    more than ``BULK_AUDIT_LIMIT`` rows rather than quietly writing a trail
    of unbounded size.
    """
    model = queryset.model
    if not signals.should_audit(model):
        return queryset.update(**values)

    attnames = {model._meta.get_field(name).attname: value for name, value in values.items()}
    for attname, value in list(attnames.items()):
        if hasattr(value, "pk"):
            attnames[attname] = value.pk
    targets = list(queryset.select_for_update()[: BULK_AUDIT_LIMIT + 1])
    if len(targets) > BULK_AUDIT_LIMIT:
        raise ValidationError(
            f"This change would affect more than {BULK_AUDIT_LIMIT} records. Narrow the selection.",
            code=9002,
        )

    rows = []
    for instance in targets:
        prior = {name: getattr(instance, name, None) for name in attnames}
        changes = signals.diff(model, prior, attnames)
        if not changes:
            continue
        for name, value in attnames.items():
            setattr(instance, name, value)
        rows.append(_audit_row(instance=instance, action=signals.classify_update(changes), changes=changes))

    updated = model._base_manager.filter(pk__in=[instance.pk for instance in targets]).update(**values)
    try:
        AuditLog.objects.bulk_create(rows, batch_size=500)
    except Exception:  # noqa: BLE001
        logger.warning("[audited_update] could not write the trail for %s", model.__name__, exc_info=True)
    return updated


# ── Endpoint-error resolution workflow ───────────────────────────────────────
def get_endpoint_log(uid) -> RequestLog:
    log = RequestLog.objects.filter(uid=uid).first()
    if log is None:
        raise NotFoundError("Endpoint log not found")
    return log


@transaction.atomic
def resolve_endpoint_error(*, uid, note: str = "", resolver=None) -> RequestLog:
    """Mark an ERROR transaction as fixed."""
    log = get_endpoint_log(uid)
    if log.level != LogLevelChoices.ERROR.value:
        raise ValidationError("Only ERROR entries carry a resolution workflow", code=NOT_AN_ERROR)
    if log.is_resolved:
        raise ValidationError("This error is already marked as resolved", code=ALREADY_RESOLVED)

    log.is_resolved = True
    log.resolved_by = resolver
    log.resolved_at = timezone.now()
    log.resolution_note = note or ""
    log.save(update_fields=["is_resolved", "resolved_by", "resolved_at", "resolution_note"])
    logger.info("[resolve_endpoint_error] %s resolved by %s", log.uid, getattr(resolver, "pk", None))
    return log


@transaction.atomic
def resolve_similar_errors(*, uid, note: str = "", resolver=None) -> int:
    """Resolve every open occurrence of the same defect (same error type on the
    same operation) in one action. Returns how many were resolved."""
    log = get_endpoint_log(uid)
    if log.level != LogLevelChoices.ERROR.value:
        raise ValidationError("Only ERROR entries carry a resolution workflow", code=NOT_AN_ERROR)
    similar = RequestLog.objects.filter(
        level=LogLevelChoices.ERROR.value,
        is_resolved=False,
        error_type=log.error_type,
        operations=log.operations,
        path=log.path,
    )
    return similar.update(
        is_resolved=True, resolved_by=resolver, resolved_at=timezone.now(), resolution_note=note or ""
    )


@transaction.atomic
def reopen_endpoint_error(*, uid) -> RequestLog:
    """Reopen a resolved error (the fix did not hold). The previous resolution
    note is kept for context; the resolver stamp is cleared."""
    log = get_endpoint_log(uid)
    if not log.is_resolved:
        raise ValidationError("This error is not resolved", code=NOT_RESOLVED)

    log.is_resolved = False
    log.resolved_by = None
    log.resolved_at = None
    log.save(update_fields=["is_resolved", "resolved_by", "resolved_at"])
    return log


# ── Error-report recipients ──────────────────────────────────────────────────
@transaction.atomic
def set_error_report_recipients(*, user_uids: list) -> list[ErrorReportSubscription]:
    """Sync the daily error-report recipient list to exactly ``user_uids``.

    Set semantics (add missing, soft-delete removed) so the client can drive
    this from a checkbox multi-select without diffing.
    """
    wanted_uids = set(user_uids or [])
    wanted = list(get_user_model().objects.filter(uid__in=wanted_uids))
    if len(wanted) != len(wanted_uids):
        raise ValidationError("One or more selected users were not found", code=9003)

    current = {s.user_id: s for s in ErrorReportSubscription.objects.select_related("user")}
    wanted_ids = {user.id for user in wanted}

    for user_id, subscription in current.items():
        if user_id not in wanted_ids:
            subscription.delete()  # soft delete — the change trail records who removed whom
    for user in wanted:
        if user.id not in current:
            ErrorReportSubscription.objects.create(user=user)

    return list(ErrorReportSubscription.objects.select_related("user").order_by("user__first_name"))


# ── Daily error report ───────────────────────────────────────────────────────
def send_error_report(*, since=None, until=None) -> dict:
    """E-mail an ERROR-level digest to every subscribed user.

    Defaults to *yesterday* (the beat task's daily window). Groups repeated
    failures by (error type, operation) so a hot loop of the same defect reads
    as one line with a count, not five hundred rows. Sends through the
    notifications pipeline (``queue``) so delivery gets the standard retry
    machinery and shows up in the notification log.
    """
    now = timezone.localtime()
    if until is None:
        until = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if since is None:
        since = until - timedelta(days=1)

    errors = RequestLog.objects.filter(
        level=LogLevelChoices.ERROR.value, created_at__gte=since, created_at__lt=until
    )
    unresolved_total = RequestLog.objects.filter(level=LogLevelChoices.ERROR.value, is_resolved=False).count()
    error_count = errors.count()

    if error_count == 0 and unresolved_total == 0:
        logger.info("[send_error_report] nothing to report — skipping")
        return {"errors": 0, "unresolved": 0, "recipients": 0, "sent": False}

    recipients = list(ErrorReportSubscription.objects.select_related("user").filter(user__is_active=True))
    if not recipients:
        logger.info("[send_error_report] %s error(s) but no recipients configured", error_count)
        return {"errors": error_count, "unresolved": unresolved_total, "recipients": 0, "sent": False}

    groups = (
        errors.values("error_type", "operations", "path")
        .annotate(occurrences=Count("id"), last_seen=Max("created_at"), last_message=Max("error_message"))
        .order_by("-occurrences")[:20]
    )
    window = f"{since:%d %b %Y %H:%M} – {until:%d %b %Y %H:%M}"
    lines = [
        f"Endpoint error report for {window}",
        "",
        f"New errors in window : {error_count}",
        f"Unresolved (all time): {unresolved_total}",
        "",
    ]
    for group in groups:
        operation = group["operations"] or group["path"]
        lines.append(
            f"• {group['error_type'] or 'Error'} in {operation} — ×{group['occurrences']} "
            f"(last {timezone.localtime(group['last_seen']):%H:%M})"
        )
        if group["last_message"]:
            lines.append(f"    {group['last_message'][:300]}")
    lines += ["", "Review each error, fix it, and mark it resolved on the Endpoint errors screen."]
    message = "\n".join(lines)
    subject = f"[{settings.PROJECT_NAME}] {error_count} endpoint error(s) — {since:%d %b %Y}"

    sent = 0
    for subscription in recipients:
        try:
            notification_services.queue(
                channel=NotificationChannelChoices.EMAIL.value,
                category=ERROR_DIGEST,
                subject=subject,
                title="Daily endpoint error report",
                message=message,
                receiver=subscription.user,
                url=routes.ENDPOINT_ERRORS,
            )
            sent += 1
        except Exception:  # noqa: BLE001 — one bad recipient must not stop the rest
            logger.warning(
                "[send_error_report] failed to queue for user %s", subscription.user_id, exc_info=True
            )

    logger.info("[send_error_report] %s error(s) reported to %s recipient(s)", error_count, sent)
    return {"errors": error_count, "unresolved": unresolved_total, "recipients": sent, "sent": sent > 0}


# ── Retention ────────────────────────────────────────────────────────────────
def purge_request_logs() -> dict:
    """Age out request logs by severity; unresolved ERRORs are never purged."""
    now = timezone.now()
    horizons = {
        LogLevelChoices.INFO.value: getattr(settings, "AUDIT_INFO_RETENTION_DAYS", 14),
        LogLevelChoices.WARNING.value: getattr(settings, "AUDIT_WARNING_RETENTION_DAYS", 60),
    }
    purged = {}
    for level, days in horizons.items():
        purged[level], _ = RequestLog.objects.filter(
            level=level, created_at__lt=now - timedelta(days=days)
        ).delete()
    resolved_days = getattr(settings, "AUDIT_RESOLVED_ERROR_RETENTION_DAYS", 180)
    purged["RESOLVED_ERROR"], _ = RequestLog.objects.filter(
        level=LogLevelChoices.ERROR.value,
        is_resolved=True,
        created_at__lt=now - timedelta(days=resolved_days),
    ).delete()
    logger.info("[purge_request_logs] %s", purged)
    return purged


def purge_audit_trail() -> int:
    """Age out the change trail. Off by default (``AUDIT_TRAIL_RETENTION_DAYS``
    = 0 keeps it for ever) — many organisations must keep it for years."""
    days = int(getattr(settings, "AUDIT_TRAIL_RETENTION_DAYS", 0) or 0)
    if days <= 0:
        return 0
    purged, _ = AuditLog.objects.filter(created_at__lt=timezone.now() - timedelta(days=days)).delete()
    logger.info("[purge_audit_trail] removed %s row(s) older than %s days", purged, days)
    return purged


# ── Dashboard counters contributed by other apps ─────────────────────────────
_stat_providers: dict[str, Callable[[], int]] = {}


def register_stat(name: str, provider: Callable[[], int]) -> None:
    """Let an app above this one fill a dashboard counter it owns the data for
    (``authentication`` contributes ``logins_today``)."""
    _stat_providers[name] = provider


def contributed_stat(name: str) -> int:
    provider = _stat_providers.get(name)
    if provider is None:
        return 0
    try:
        return int(provider())
    except Exception:  # noqa: BLE001
        logger.warning("[audit] stat provider '%s' failed", name, exc_info=True)
        return 0


__all__ = [
    "AuditActionChoices",
    "audited_update",
    "contributed_stat",
    "purge_audit_trail",
    "purge_request_logs",
    "record_audit",
    "record_request_log",
    "register_stat",
    "reopen_endpoint_error",
    "resolve_endpoint_error",
    "resolve_similar_errors",
    "send_error_report",
    "set_error_report_recipients",
]
