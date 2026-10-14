"""Read logic for files and the attachment-type catalogue (never mutates)."""

from django.conf import settings
from django.db.models import Q, QuerySet, Sum

from apps.attachments.constants import (
    ALLOWED_DOCUMENT_EXTS,
    ALLOWED_IMAGE_EXTS,
    EXTENSION_LABELS,
    MIME_BY_EXT,
    FileCategoryChoices,
)
from apps.attachments.models import Attachment, AttachmentDefinition
from apps.core.exceptions import NotFoundError, ValidationError


# ── Files ────────────────────────────────────────────────────────────────────
def get_attachment(uid) -> Attachment:
    attachment = Attachment.objects.filter(uid=uid).first()
    if attachment is None:
        raise NotFoundError("We couldn't find that file. It may have been removed.")
    return attachment


def storage_key_from_path(media_path: str) -> str:
    """The storage key behind a media path (``/media/profiles/…`` → ``profiles/…``)."""
    if not media_path or not str(media_path).strip():
        raise ValidationError("No file path was provided.", code=9005)
    key = str(media_path).strip()
    if key.startswith(settings.MEDIA_URL):
        key = key[len(settings.MEDIA_URL) :]
    key = key.lstrip("/")
    # Never let a path climb out of the storage root.
    if ".." in key.split("/") or "\\" in key or key.startswith("/"):
        raise ValidationError("That file path is not allowed.", code=9002)
    return key


def get_attachment_by_path(media_path: str) -> Attachment:
    """The file registered at ``media_path``.

    Only files this system stored have a row; anything else under the media
    root is unknown here and therefore cannot be read through the API.
    """
    key = storage_key_from_path(media_path)
    attachment = Attachment.objects.filter(storage_key=key).first()
    if attachment is None:
        raise NotFoundError("We couldn't find that file. It may have been removed.")
    return attachment


def attachments_queryset(filters: Q | None = None) -> QuerySet[Attachment]:
    qs = Attachment.objects.select_related("created_by", "definition")
    return qs.filter(filters) if filters is not None else qs


def attachments_of(*, entity_type: str, entity_uid) -> QuerySet[Attachment]:
    return attachments_queryset(Q(entity_type=entity_type, entity_uid=entity_uid))


# ── Attachment types ─────────────────────────────────────────────────────────
def attachment_definitions_queryset(filters: Q | None = None) -> QuerySet[AttachmentDefinition]:
    qs = AttachmentDefinition.objects.all()
    return qs.filter(filters) if filters is not None else qs


def get_attachment_definition(uid) -> AttachmentDefinition:
    definition = AttachmentDefinition.objects.filter(uid=uid).first()
    if definition is None:
        raise NotFoundError("Attachment definition not found")
    return definition


def active_attachment_definitions() -> list[AttachmentDefinition]:
    """The pickable catalogue used by forms that ask for documents."""
    return list(AttachmentDefinition.objects.filter(is_active=True).order_by("name_en"))


def allowed_extensions_for(kind: str) -> set[str]:
    """The file types the system accepts for a definition of ``kind``."""
    return ALLOWED_IMAGE_EXTS if kind == FileCategoryChoices.Image.value else ALLOWED_DOCUMENT_EXTS


def allowed_file_extensions() -> list[dict]:
    """The pickable extension catalogue, per kind — built from the same
    constants the upload endpoint enforces, so the list can never drift."""
    rows = []
    for kind, extensions in (
        (FileCategoryChoices.Document.value, ALLOWED_DOCUMENT_EXTS),
        (FileCategoryChoices.Image.value, ALLOWED_IMAGE_EXTS),
    ):
        for extension in sorted(extensions):
            rows.append(
                {
                    "kind": kind,
                    "extension": extension,
                    "label": EXTENSION_LABELS.get(extension, extension.upper()),
                    "mime": MIME_BY_EXT.get(extension, "application/octet-stream"),
                }
            )
    return rows


def attachment_stats() -> dict:
    """Counter block for the Attachment types dashboard: the catalogue by kind
    and use, and what is actually stored."""
    definitions = AttachmentDefinition.objects
    files = Attachment.objects
    image, document = FileCategoryChoices.Image.value, FileCategoryChoices.Document.value
    return {
        "definitions_total": definitions.count(),
        "definitions_active": definitions.filter(is_active=True).count(),
        "definitions_documents": definitions.filter(kind=document).count(),
        "definitions_images": definitions.filter(kind=image).count(),
        "definitions_in_use": (
            files.filter(definition__isnull=False).values("definition").distinct().count()
        ),
        "files_total": files.count(),
        "files_images": files.filter(file_type=image).count(),
        "files_documents": files.filter(file_type=document).count(),
        "storage_bytes": files.aggregate(total=Sum("size"))["total"] or 0,
    }
