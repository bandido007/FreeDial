"""Tabular exports — one listing → PDF / Excel / CSV / Word bytes.

Admin listings need "this grid, as a file": a branded header and a zebra table
driven by ``columns`` + ``rows`` only, so every listing (users, roles, audit
rows, …) exports through one code path instead of growing its own. Callers get
``(bytes, filename, mime)`` and hand the bytes back to the client
base64-encoded inside the standard envelope (:class:`ExportFileType`), which
keeps the download on the authenticated GraphQL channel — no signed URL, no
temp file, nothing to clean up.

Usage in a resolver::

    export = build_table(title="Users", columns=["Name", "E-mail"], rows=rows, fmt=fmt)
    return Response.build_response(code=9001, info=info, data=export.as_payload())

Rendering libraries are imported lazily so the cheap formats (CSV) never pay
for reportlab / openpyxl / python-docx start-up.
"""

import base64
import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime

import graphene
from django.conf import settings

from apps.core.enums import BaseEnum

# Document palette — every file the system emits reads as one family of
# official records. The accent and the brand line come from settings, so a
# project is rebranded without touching this module.
INK = "#1F2937"
MUTED = "#6B7280"
DEFAULT_ACCENT = "#0F5C50"


def _brand() -> str:
    """The line printed above every export's title."""
    brand = getattr(settings, "EMAIL_BRAND", {}) or {}
    parts = [brand.get("portal") or getattr(settings, "PROJECT_NAME", ""), brand.get("organisation")]
    return " — ".join(part for part in parts if part)


def _accent() -> str:
    """Header colour, as ``#RRGGBB``."""
    accent = (getattr(settings, "EMAIL_BRAND", {}) or {}).get("accent") or DEFAULT_ACCENT
    return accent if re.fullmatch(r"#[0-9A-Fa-f]{6}", accent) else DEFAULT_ACCENT


class TableExportFormatChoices(BaseEnum):
    PDF = "PDF"
    EXCEL = "EXCEL"
    CSV = "CSV"
    WORD = "WORD"


TableExportFormatEnum = graphene.Enum.from_enum(TableExportFormatChoices)


class ExportFileType(graphene.ObjectType):
    """A generated file, inline. Decode ``contentBase64`` into a Blob client-side."""

    file_name = graphene.String()
    mime_type = graphene.String()
    size_bytes = graphene.Int()
    content_base64 = graphene.String()


@dataclass(frozen=True)
class ExportFile:
    payload: bytes
    file_name: str
    mime_type: str

    def as_payload(self) -> dict:
        """The :class:`ExportFileType` shape (a dict — graphene resolves keys)."""
        return {
            "file_name": self.file_name,
            "mime_type": self.mime_type,
            "size_bytes": len(self.payload),
            "content_base64": base64.b64encode(self.payload).decode(),
        }


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        return f"{value:%d %b %Y %H:%M}"
    return str(value)


def _normalise(rows) -> list[list[str]]:
    return [[_cell(v) for v in row] for row in rows]


# ══════════════════════════════════════════════════════════════════════════
# CSV — the lingua franca; no dependency
# ══════════════════════════════════════════════════════════════════════════
def _build_csv(title: str, subtitle: str, columns: list[str], rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    # UTF-8 BOM so Excel on Windows opens accented names correctly.
    return ("﻿" + buffer.getvalue()).encode("utf-8")


# ══════════════════════════════════════════════════════════════════════════
# Excel (openpyxl)
# ══════════════════════════════════════════════════════════════════════════
def _build_xlsx(title: str, subtitle: str, columns: list[str], rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = _sheet_name(title)
    sheet.append([title])
    sheet["A1"].font = Font(bold=True, size=14)
    if subtitle:
        sheet.append([subtitle])
        sheet.cell(row=2, column=1).font = Font(color=MUTED.lstrip("#"))
    sheet.append([])
    sheet.append(columns)
    header_fill = PatternFill("solid", fgColor=_accent().lstrip("#"))
    header_font = Font(bold=True, color="FFFFFF")
    for cell in sheet[sheet.max_row]:
        cell.fill, cell.font = header_fill, header_font
    sheet.freeze_panes = sheet.cell(row=sheet.max_row + 1, column=1)
    for row in rows:
        sheet.append(row)
    # Width from the longest value per column (bounded so one long cell can't
    # blow the sheet out).
    for index, column in enumerate(columns, start=1):
        longest = max([len(column)] + [len(r[index - 1]) for r in rows if index - 1 < len(r)])
        sheet.column_dimensions[get_column_letter(index)].width = min(max(12, longest + 2), 48)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _sheet_name(title: str) -> str:
    return re.sub(r"[\[\]:*?/\\]", "", title)[:28] or "Export"


# ══════════════════════════════════════════════════════════════════════════
# PDF (reportlab / platypus) — landscape when the table is wide
# ══════════════════════════════════════════════════════════════════════════
def _build_pdf(title: str, subtitle: str, columns: list[str], rows: list[list[str]]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4) if len(columns) > 5 else A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=title,
    )
    styles = getSampleStyleSheet()
    brand = ParagraphStyle("brand", parent=styles["Normal"], fontSize=9, textColor=MUTED)
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=16, textColor=INK, spaceAfter=2, alignment=0)
    meta = ParagraphStyle("meta", parent=styles["Normal"], fontSize=9.5, textColor=MUTED)
    cell_style = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8, leading=10, textColor=INK)
    head_style = ParagraphStyle("head", parent=cell_style, textColor=colors.white, fontName="Helvetica-Bold")

    story = [Paragraph(_esc(_brand()), brand), Spacer(1, 4), Paragraph(_esc(title), h1)]
    lines = [subtitle] if subtitle else []
    lines.append(f"{len(rows)} row(s) · exported {datetime.now():%d %b %Y %H:%M}")
    story.append(Paragraph(" · ".join(_esc(line) for line in lines), meta))
    story.append(Spacer(1, 8))

    # Paragraph cells wrap long text instead of overflowing the page edge.
    data = [[Paragraph(_esc(c), head_style) for c in columns]]
    data += [[Paragraph(_esc(v), cell_style) for v in row] for row in rows]
    table = Table(data, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_accent())),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6F4")]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D7DDD9")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    return buffer.getvalue()


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ══════════════════════════════════════════════════════════════════════════
# Word (python-docx)
# ══════════════════════════════════════════════════════════════════════════
def _build_docx(title: str, subtitle: str, columns: list[str], rows: list[list[str]]) -> bytes:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.shared import Pt, RGBColor

    muted = RGBColor(0x6B, 0x72, 0x80)
    doc = Document()
    if len(columns) > 5:
        section = doc.sections[0]
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = section.page_height, section.page_width

    run = doc.add_paragraph().add_run(_brand())
    run.font.size, run.font.color.rgb = Pt(9), muted
    doc.add_heading(title, level=0)
    lines = [subtitle] if subtitle else []
    lines.append(f"{len(rows)} row(s) · exported {datetime.now():%d %b %Y %H:%M}")
    run = doc.add_paragraph().add_run(" · ".join(lines))
    run.font.size, run.font.color.rgb = Pt(9.5), muted

    table = doc.add_table(rows=1, cols=len(columns))
    table.style = "Light Grid Accent 6"
    for index, column in enumerate(columns):
        cell = table.rows[0].cells[index]
        cell.text = column
        for paragraph in cell.paragraphs:
            for r in paragraph.runs:
                r.bold = True
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row[: len(columns)]):
            cells[index].text = value

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════
_BUILDERS = {
    TableExportFormatChoices.CSV.value: (_build_csv, "csv", "text/csv"),
    TableExportFormatChoices.EXCEL.value: (
        _build_xlsx,
        "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    TableExportFormatChoices.PDF.value: (_build_pdf, "pdf", "application/pdf"),
    TableExportFormatChoices.WORD.value: (
        _build_docx,
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
}


def build_table(*, title: str, columns: list[str], rows, fmt: str, subtitle: str = "") -> ExportFile:
    """Render a listing → :class:`ExportFile` in ``fmt`` (a ``TableExportFormatChoices`` value).

    ``rows`` is any iterable of sequences (values are stringified; ``None`` → "",
    booleans → Yes/No, datetimes → ``dd Mon YYYY HH:MM``). Raises ``KeyError`` for
    an unknown format — validate at the API edge with :func:`is_supported`.
    """
    builder, extension, mime = _BUILDERS[fmt]
    payload = builder(title, subtitle, list(columns), _normalise(rows))
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()[:60] or "export"
    file_name = f"{slug}-{datetime.now():%Y%m%d-%H%M}.{extension}"
    return ExportFile(payload=payload, file_name=file_name, mime_type=mime)


def is_supported(fmt) -> bool:
    return fmt in _BUILDERS
