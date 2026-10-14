"""Audit read logic (never mutates)."""

from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.audit import services
from apps.audit.enums import LogLevelChoices
from apps.audit.models import AuditLog, ErrorReportSubscription, RequestLog


def audit_logs_queryset(filters: Q | None = None) -> QuerySet[AuditLog]:
    qs = AuditLog.objects.select_related("actor").order_by("-created_at")
    return qs.filter(filters) if filters is not None else qs


def my_activity_queryset(user, filters: Q | None = None) -> QuerySet[AuditLog]:
    """The signed-in user's own action trail — scoped server-side, always."""
    return audit_logs_queryset(filters).filter(actor=user)


def record_history_queryset(*, entity: str, object_uid) -> QuerySet[AuditLog]:
    """Everything that ever happened to one record, oldest first."""
    return AuditLog.objects.filter(entity__iexact=entity, object_uid=object_uid).order_by("created_at")


def request_logs_queryset(filters: Q | None = None) -> QuerySet[RequestLog]:
    qs = RequestLog.objects.select_related("user", "resolved_by").order_by("-created_at")
    return qs.filter(filters) if filters is not None else qs


def endpoint_errors_queryset(filters: Q | None = None) -> QuerySet[RequestLog]:
    """ERROR-level transactions only — the contract of the Endpoint errors
    screen (INFO/WARNING never surface there, whatever the client sends)."""
    return request_logs_queryset(filters).filter(level=LogLevelChoices.ERROR.value)


def error_report_recipients() -> QuerySet[ErrorReportSubscription]:
    return ErrorReportSubscription.objects.select_related("user").order_by("user__first_name")


def audited_entities() -> list[str]:
    """The model labels that appear in the trail (for the filter drop-down)."""
    return sorted(AuditLog.objects.order_by().values_list("entity", flat=True).distinct())


def audit_stats() -> dict:
    """Counter block for the audit dashboard (one cheap query each)."""
    today = timezone.localdate()
    error = LogLevelChoices.ERROR.value
    return {
        "actions_today": AuditLog.objects.filter(created_at__date=today).count(),
        "actions_total": AuditLog.objects.count(),
        "actors_today": (
            AuditLog.objects.filter(created_at__date=today, actor__isnull=False)
            .values("actor")
            .distinct()
            .count()
        ),
        "logins_today": services.contributed_stat("logins_today"),
        "failed_logins_today": services.contributed_stat("failed_logins_today"),
        "errors_today": RequestLog.objects.filter(level=error, created_at__date=today).count(),
        "unresolved_errors": RequestLog.objects.filter(level=error, is_resolved=False).count(),
    }
