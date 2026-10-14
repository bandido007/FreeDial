"""Attachment constants: file categories, allowed types, purposes.

Kept small and declarative so the service layer stays readable and limits are
tuned in one place.
"""

import graphene

from apps.core.enums import BaseEnum


class FileCategoryChoices(BaseEnum):
    """Coarse file category — drives which validation applies."""

    Image = "Image"
    Document = "Document"


FileCategoryEnum = graphene.Enum.from_enum(FileCategoryChoices)


class ScanStatusChoices(BaseEnum):
    SKIPPED = "SKIPPED"  # no scanner configured
    CLEAN = "CLEAN"
    UNSCANNED = "UNSCANNED"  # scanner unreachable and configured to let files through


# ── Allowed extensions per category ──────────────────────────────────────────
ALLOWED_IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}
ALLOWED_DOCUMENT_EXTS = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "txt"}

# Map a stored extension back to a MIME type for the download response.
MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "csv": "text/csv",
    "txt": "text/plain",
}

# Human labels for the extension catalogue the settings screen offers.
EXTENSION_LABELS = {
    "png": "PNG image",
    "jpg": "JPEG image",
    "jpeg": "JPEG image (.jpeg)",
    "webp": "WebP image",
    "gif": "GIF image",
    "pdf": "PDF document",
    "doc": "Word document (.doc)",
    "docx": "Word document (.docx)",
    "xls": "Excel workbook (.xls)",
    "xlsx": "Excel workbook (.xlsx)",
    "ppt": "PowerPoint presentation (.ppt)",
    "pptx": "PowerPoint presentation (.pptx)",
    "csv": "CSV spreadsheet",
    "txt": "Plain text",
}

# Largest edge (px) an uploaded image is normalised to — keeps avatars small,
# strips embedded metadata, and re-encodes to a safe format.
IMAGE_MAX_DIMENSION = 1600
# Refuse an image whose decoded size would exhaust memory (decompression bomb).
IMAGE_MAX_PIXELS = 40_000_000

# ── Purposes ─────────────────────────────────────────────────────────────────
# What a file is *for*. The purpose picks the folder and — more importantly —
# the rule for who may read the file (see policies.py).
PURPOSE_GENERAL = "general"
PURPOSE_PROFILE_PHOTO = "profile-photo"
PURPOSE_PUBLIC = "public"

BUCKET_BY_PURPOSE = {
    PURPOSE_GENERAL: "documents",
    PURPOSE_PROFILE_PHOTO: "profiles",
    PURPOSE_PUBLIC: "public",
}
DEFAULT_BUCKET = "documents"
