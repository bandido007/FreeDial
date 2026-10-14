"""Audit & system logs — the append-only record of what happened.

Three concerns live here:

* :class:`AuditLog` — one row per data change (create / update / soft-delete /
  restore / hard delete) on any audited model, with a field-level ``{from, to}``
  diff, the acting user, and the request ID. Written centrally by the
  ``pre_save`` / ``post_save`` / ``post_delete`` receivers in
  ``apps.audit.signals`` — no service has to remember to write it.
* :class:`RequestLog` — one row per API transaction (GraphQL query/mutation or
  REST call) with duration, status and severity. ERROR rows carry the unhandled
  exception + traceback and drive the "Endpoint errors" screen, where an
  administrator marks them resolved once the fix ships.
* :class:`ErrorReportSubscription` — the recipient list of the daily
  error-report e-mail (see ``apps.audit.tasks``).

``AuditLog`` and ``RequestLog`` are deliberately **plain ``models.Model``**, not
:class:`~apps.core.models.BaseModel`: log rows are immutable, must never be
soft-deleted or user-stamped, and (because the change receivers only watch
``BaseModel`` subclasses) this keeps the trail from auditing itself.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.audit.enums import AuditActionChoices, LogLevelChoices, RequestKindChoices
from apps.core.models import BaseModel
from apps.core.utils import generate_uuid7


class AuditLog(models.Model):
    """Append-only, field-level change trail for every audited record."""

    uid = models.UUIDField(default=generate_uuid7, unique=True, editable=False, db_index=True)

    # Actor — FK for joins, plus snapshots so the trail outlives the account.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="audit_logs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    actor_email = models.CharField(max_length=255, blank=True, db_index=True)
    actor_name = models.CharField(max_length=255, blank=True)

    action = models.CharField(max_length=20, choices=AuditActionChoices.choices(), db_index=True)

    # What was touched — label pieces are denormalised for cheap filtering.
    entity = models.CharField(max_length=100, db_index=True, help_text='Model label, e.g. "users.User".')
    app_label = models.CharField(max_length=50, db_index=True)
    model_name = models.CharField(max_length=50, db_index=True)
    object_uid = models.UUIDField(null=True, blank=True, db_index=True)
    object_pk = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)

    # {"field": {"from": old, "to": new}} — sensitive values masked at capture.
    changes = models.JSONField(default=dict, blank=True)

    # Where the change came from.
    request_id = models.CharField(max_length=64, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "audit_logs"
        ordering = ["-created_at"]
        verbose_name_plural = "Audit logs"
        indexes = [
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["entity", "object_uid"]),
        ]

    def __str__(self):
        return f"{self.actor_email or 'system'} · {self.action} · {self.entity} · {self.object_repr}"


class RequestLog(models.Model):
    """One API transaction — GraphQL operation or REST call — with severity.

    ERROR rows additionally carry a resolution workflow: an administrator marks
    the error resolved (with a note) once the underlying defect is fixed.
    """

    uid = models.UUIDField(default=generate_uuid7, unique=True, editable=False, db_index=True)
    request_id = models.CharField(max_length=64, blank=True, db_index=True)

    kind = models.CharField(max_length=10, choices=RequestKindChoices.choices(), db_index=True)
    # Top-level GraphQL field(s) served, or the REST path for kind=Rest.
    operations = models.CharField(max_length=500, blank=True, db_index=True)
    operation_name = models.CharField(max_length=255, blank=True)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=255, db_index=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="request_logs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    user_email = models.CharField(max_length=255, blank=True, db_index=True)

    status_code = models.PositiveSmallIntegerField(default=200)
    level = models.CharField(max_length=10, choices=LogLevelChoices.choices(), db_index=True)
    duration_ms = models.PositiveIntegerField(default=0)

    # First unexpected failure (denormalised for list screens) …
    error_type = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)
    traceback = models.TextField(blank=True)
    # … and every failure captured during the transaction, handled or not.
    errors = models.JSONField(default=list, blank=True)

    # Sanitised GraphQL variables / POST keys (secrets redacted at capture).
    variables = models.JSONField(default=dict, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    # Resolution workflow — meaningful for level=ERROR rows only.
    is_resolved = models.BooleanField(default=False, db_index=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="resolved_request_logs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "request_logs"
        ordering = ["-created_at"]
        verbose_name_plural = "Request logs"
        indexes = [
            models.Index(fields=["level", "is_resolved", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self):
        return f"[{self.level}] {self.method} {self.operations or self.path} ({self.status_code})"


class ErrorReportSubscription(BaseModel):
    """A user subscribed to the daily endpoint-error report e-mail.

    A regular ``BaseModel`` (not a log): it is administrator-managed
    configuration, so the who-changed-it stamps and soft delete are exactly
    what is wanted — and the change receivers audit edits to the recipient list
    for free.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="error_report_subscriptions",
        on_delete=models.CASCADE,
    )

    class Meta:
        db_table = "error_report_subscriptions"
        ordering = ["-created_at"]
        verbose_name_plural = "Error report subscriptions"
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(deleted=False),
                name="unique_active_error_report_subscription",
            )
        ]

    def __str__(self):
        return f"error-report → {self.user}"
