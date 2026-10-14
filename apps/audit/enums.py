"""Enumerations for the audit domain."""

import graphene

from apps.core.enums import BaseEnum


class AuditActionChoices(BaseEnum):
    """What happened to the audited record.

    ``SoftDeleted`` / ``Restored`` / ``Deactivated`` / ``Activated`` are
    classified out of the field diff (the ``deleted`` / ``active`` flags on
    :class:`~apps.core.models.BaseModel`) so the trail reads as intent, not as
    an opaque boolean flip. ``Deleted`` is a *hard* delete (rare by design).
    """

    Created = "Created"
    Updated = "Updated"
    SoftDeleted = "SoftDeleted"
    Restored = "Restored"
    Deactivated = "Deactivated"
    Activated = "Activated"
    Deleted = "Deleted"


class RequestKindChoices(BaseEnum):
    """The shape of the API transaction being logged."""

    Query = "Query"  # GraphQL query
    Mutation = "Mutation"  # GraphQL mutation
    Rest = "Rest"  # REST endpoint (token flows, file I/O)


class LogLevelChoices(BaseEnum):
    """Severity of one endpoint transaction.

    * ``INFO``    — completed normally.
    * ``WARNING`` — an *expected* failure (business-rule violation, bad input,
      failed sign-in). Nothing for a developer to fix.
    * ``ERROR``   — an *unexpected* failure (unhandled exception, 5xx). These
      are the rows the Endpoint Errors screen surfaces for the backend team.
    """

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


AuditActionEnum = graphene.Enum.from_enum(AuditActionChoices)
RequestKindEnum = graphene.Enum.from_enum(RequestKindChoices)
LogLevelEnum = graphene.Enum.from_enum(LogLevelChoices)
