"""The shared tabular exporter renders every format from one row set."""

import base64
from datetime import datetime

import pytest

from apps.core import exports

COLUMNS = ["Name", "Type", "Verified", "Members", "Added"]
ROWS = [
    ("Central Office", "Branch", True, 12, datetime(2026, 9, 1, 10, 30)),
    ("Acme <Ltd> & Co", "Company", False, None, None),
]


@pytest.mark.parametrize(
    "fmt,extension,magic",
    [
        ("CSV", "csv", b"\xef\xbb\xbf"),
        ("EXCEL", "xlsx", b"PK"),
        ("PDF", "pdf", b"%PDF"),
        ("WORD", "docx", b"PK"),
    ],
)
def test_build_table_renders_each_format(fmt, extension, magic):
    export = exports.build_table(title="Branch offices", columns=COLUMNS, rows=ROWS, fmt=fmt)
    assert export.payload.startswith(magic)
    assert export.file_name.startswith("branch-offices-")
    assert export.file_name.endswith("." + extension)
    assert export.mime_type == exports._BUILDERS[fmt][2]


def test_csv_normalises_values_and_escapes_nothing_else():
    export = exports.build_table(title="t", columns=COLUMNS, rows=ROWS, fmt="CSV")
    text = export.payload.decode("utf-8").lstrip("﻿").splitlines()
    assert text[0] == "Name,Type,Verified,Members,Added"
    assert text[1] == "Central Office,Branch,Yes,12,01 Sep 2026 10:30"
    assert text[2] == "Acme <Ltd> & Co,Company,No,,"


def test_payload_shape_matches_the_graphql_type():
    export = exports.build_table(title="t", columns=["A"], rows=[("x",)], fmt="CSV")
    payload = export.as_payload()
    assert set(payload) == {"file_name", "mime_type", "size_bytes", "content_base64"}
    assert base64.b64decode(payload["content_base64"]) == export.payload
    assert payload["size_bytes"] == len(export.payload)


def test_unknown_format_is_reported_not_rendered():
    assert exports.is_supported("PDF")
    assert not exports.is_supported("TXT")
    with pytest.raises(KeyError):
        exports.build_table(title="t", columns=["A"], rows=[], fmt="TXT")


def test_brand_and_accent_come_from_settings(settings):
    settings.PROJECT_NAME = "Records"
    settings.EMAIL_BRAND = {"portal": "Records Office", "organisation": "City Council", "accent": "#123456"}
    assert exports._brand() == "Records Office — City Council"
    assert exports._accent() == "#123456"
    settings.EMAIL_BRAND = {"accent": "not-a-colour"}
    assert exports._accent() == exports.DEFAULT_ACCENT
    assert exports._brand() == "Records"
