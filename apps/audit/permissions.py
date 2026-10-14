"""Permissions that guard the audit screens."""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_AUDIT_LOGS = "can_view_audit_logs"
VIEW_ENDPOINT_LOGS = "can_view_endpoint_logs"
MANAGE_ENDPOINT_LOGS = "can_manage_endpoint_logs"
EXPORT_AUDIT_LOGS = "can_export_audit_logs"

GROUPS = [
    Group(
        name="Audit & System Logs",
        description="Who changed what, and which requests failed.",
        permissions=[
            Perm(
                VIEW_AUDIT_LOGS,
                "View the activity trail",
                "See every change made to the system's records: who, when, and the old and new "
                "values. Read-only.",
            ),
            Perm(
                VIEW_ENDPOINT_LOGS,
                "View endpoint errors",
                "See the requests that failed unexpectedly, with their technical details. Read-only.",
            ),
            Perm(
                MANAGE_ENDPOINT_LOGS,
                "Resolve endpoint errors",
                "Mark an error as fixed or reopen it, and choose who receives the daily error report.",
            ),
            Perm(
                EXPORT_AUDIT_LOGS,
                "Export the activity trail",
                "Download the activity trail as a PDF, Excel, CSV or Word file. Read-only, but the "
                "file holds personal data.",
            ),
        ],
    ),
]

ROLES = [
    RoleSpec(
        name="Security Auditor",
        permissions=[VIEW_AUDIT_LOGS, VIEW_ENDPOINT_LOGS, EXPORT_AUDIT_LOGS],
    ),
]
