"""Reports in the admin (admin -> Reports): a period, three tables, and each table as a file."""

from datetime import date

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.template.response import TemplateResponse
from django.urls import path

from apps.core.exports import build_table, is_supported
from apps.rbac.admin_access import RbacAdminMixin
from apps.reports.models import Report
from apps.reports.permissions import VIEW_REPORTS
from apps.reports.selectors import PRESETS, build_report, period
from apps.reports.tables import SECTIONS, tables

FORMATS = {"csv": "CSV", "xlsx": "EXCEL", "pdf": "PDF"}  # file format codes for build_table
LABELS = {"csv": "CSV", "xlsx": "Excel", "pdf": "PDF"}


def _period(request):
    preset = request.GET.get("period", "today")
    try:
        start = date.fromisoformat(request.GET["since"]) if request.GET.get("since") else None
        end = date.fromisoformat(request.GET["until"]) if request.GET.get("until") else None
        since, until = period(preset if preset in PRESETS else "today", start, end)
    except ValueError:
        preset, start, end = "today", None, None
        since, until = period("today")
    return preset, since, until, start, end


@admin.register(Report)
class ReportAdmin(RbacAdminMixin, admin.ModelAdmin):
    view_permission = VIEW_REPORTS
    manage_permission = "-"  # nothing to edit: reports are read from the event log

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        export = self.admin_site.admin_view(self.export_view)
        return [
            path("export/<str:section>.<str:extension>", export, name="reports_export"),
            *super().get_urls(),
        ]

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        preset, since, until, start, end = _period(request)
        report = build_report(since=since, until=until)
        query = request.GET.urlencode()
        context = {
            **self.admin_site.each_context(request),
            "title": "Reports",
            "opts": self.model._meta,
            "presets": PRESETS,
            "preset": preset,
            "start": (start or since.date()).isoformat(),
            "end": (end or report.until.date()).isoformat(),
            "since": since,
            "until": report.until,
            "tables": [(section, table) for section, table in tables(report).items()],
            "formats": LABELS,
            "query": query,
        }
        return TemplateResponse(request, "reports/report.html", context)

    def export_view(self, request, section, extension):
        if not self.has_view_permission(request):
            raise PermissionDenied
        fmt = FORMATS.get(extension)
        if section not in SECTIONS or fmt is None or not is_supported(fmt):
            raise Http404("Unknown report section or file format.")
        _preset, since, until, _start, _end = _period(request)
        report = build_report(since=since, until=until)
        table = tables(report)[section]
        subtitle = f"{since:%d %b %Y %H:%M} to {report.until:%d %b %Y %H:%M}"
        export = build_table(
            title=table.title, subtitle=subtitle, columns=table.columns, rows=table.rows, fmt=fmt
        )
        response = HttpResponse(export.payload, content_type=export.mime_type)
        response["Content-Disposition"] = f'attachment; filename="{export.file_name}"'
        return response
