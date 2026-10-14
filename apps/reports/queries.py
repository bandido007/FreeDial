"""GraphQL queries of the reports app."""

from datetime import date

import graphene

from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.exports import ExportFileType, TableExportFormatEnum, build_table
from apps.core.graphql.response import Response
from apps.reports.permissions import VIEW_REPORTS
from apps.reports.selectors import PRESETS, build_report, period
from apps.reports.tables import SECTIONS, tables
from apps.reports.types import ReportType

PERIOD_HELP = f"One of {', '.join(PRESETS)}; custom takes since and until (dates, both days included)."


def _report(preset: str, since: date | None, until: date | None):
    try:
        start, end = period(preset, since, until)
    except ValueError:
        return None
    return build_report(since=start, until=end)


class Query(graphene.ObjectType):
    get_report = Response(
        ReportType,
        period=graphene.String(default_value="today", description=PERIOD_HELP),
        since=graphene.Date(),
        until=graphene.Date(),
        description="Calls per queue, service level, abandon rate, handle time, agent time in state, and the "
        "AI's containment and handoff reasons, from the event log. permissions=['can_view_reports']",
    )
    export_report = Response(
        ExportFileType,
        section=graphene.String(required=True, description=f"One of {', '.join(SECTIONS)}"),
        format=TableExportFormatEnum(required=True),
        period=graphene.String(default_value="today", description=PERIOD_HELP),
        since=graphene.Date(),
        until=graphene.Date(),
        description="One report table as a file (PDF, Excel, CSV or Word). permissions=['can_view_reports']",
    )

    @staticmethod
    @has_permission(permissions=[VIEW_REPORTS])
    @log_errors_and_warnings
    def resolve_get_report(root, info, period="today", since=None, until=None):
        report = _report(period, since, until)
        if report is None:
            return Response.build_response(code=10900, info=info)
        return Response.build_response(code=9001, info=info, data=report)

    @staticmethod
    @has_permission(permissions=[VIEW_REPORTS])
    @log_errors_and_warnings
    def resolve_export_report(root, info, section, format, period="today", since=None, until=None):
        report = _report(period, since, until)
        if report is None:
            return Response.build_response(code=10900, info=info)
        if section not in SECTIONS:
            return Response.build_response(code=10901, info=info)
        table = tables(report)[section]
        subtitle = f"{report.since:%d %b %Y %H:%M} to {report.until:%d %b %Y %H:%M}"
        fmt = format.value if hasattr(format, "value") else str(format)
        export = build_table(
            title=table.title, subtitle=subtitle, columns=table.columns, rows=table.rows, fmt=fmt
        )
        return Response.build_response(code=9001, info=info, data=export.as_payload())
