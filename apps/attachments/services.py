"""Write-side logic for files (store, read, delete) and the attachment-type catalogue.

What an upload goes through, in order:

1. **Size** — refused before anything else is done with the content.
2. **Extension** — must be on the allow-list for its category (and for the
   attachment type, when one is named).
3. **Content** — images are decoded, auto-oriented, resized and re-encoded by
   Pillow, which strips EXIF/GPS metadata and rejects files that merely
   *claim* to be images. Documents must carry the signature of their type.
4. **Virus scan** — when a scanner is configured.
5. **Storage** — under a UUIDv7 name, never the uploader's, so a crafted file
   name can neither traverse nor overwrite anything.

Every failure raises a ``ValidationError`` whose message is safe to show a
person verbatim.
"""

import base64
import binascii
import hashlib
import io
import logging
import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.attachments import policies, scanner, selectors, sniffing
from apps.attachments.constants import (
    ALLOWED_DOCUMENT_EXTS,
    ALLOWED_IMAGE_EXTS,
    IMAGE_MAX_DIMENSION,
    IMAGE_MAX_PIXELS,
    MIME_BY_EXT,
    PURPOSE_GENERAL,
    PURPOSE_PUBLIC,
    FileCategoryChoices,
)
from apps.attachments.models import Attachment, AttachmentDefinition
from apps.attachments.permissions import DELETE_ATTACHMENTS, PUBLISH_PUBLIC_FILES
from apps.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from apps.core.hooks import resolve_permissions
from apps.core.utils import generate_uuid7

logger = logging.getLogger(__name__)

TOO_LARGE = 9180
TYPE_NOT_ACCEPTED = 9181
CONTENT_MISMATCH = 9182
NOT_ALLOWED = 9184
NOT_SAVED = 9185

# Pillow save format keyed by normalised extension.
PIL_FORMAT = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP", "gif": "GIF"}
MEGABYTE = 1024 * 1024

Image.MAX_IMAGE_PIXELS = IMAGE_MAX_PIXELS


# ── Limits ───────────────────────────────────────────────────────────────────
def max_bytes(category: str, definition: AttachmentDefinition | None = None) -> int:
    if category == FileCategoryChoices.Image.value:
        ceiling = int(getattr(settings, "ATTACHMENT_MAX_IMAGE_MB", 5)) * MEGABYTE
    else:
        ceiling = int(getattr(settings, "ATTACHMENT_MAX_DOCUMENT_MB", 15)) * MEGABYTE
    if definition is not None and definition.max_size_mb:
        ceiling = min(ceiling, definition.max_size_mb * MEGABYTE)
    return ceiling


def _category(file_type) -> str:
    value = getattr(file_type, "value", file_type)
    if value in (FileCategoryChoices.Image.value, "image", None, ""):
        return FileCategoryChoices.Image.value
    return FileCategoryChoices.Document.value


def _extension(file_name: str) -> str:
    return os.path.splitext(file_name or "")[1].lower().lstrip(".")


def decode_base64(base64_string: str) -> bytes:
    """Decode a base64 payload, tolerating a ``data:...;base64,`` prefix."""
    if not base64_string or not base64_string.strip():
        raise ValidationError("No file content was provided.", code=9005)
    payload = base64_string.strip()
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as err:
        raise ValidationError("The uploaded file could not be read. Please try again.", code=9002) from err


# ── Content ──────────────────────────────────────────────────────────────────
def process_image(raw_bytes: bytes, extension: str) -> tuple[bytes, str]:
    """Validate + normalise an image. Returns (bytes, final_extension)."""
    try:
        Image.open(io.BytesIO(raw_bytes)).verify()
        # verify() leaves the image unusable — reopen for the actual transform.
        image: Image.Image = Image.open(io.BytesIO(raw_bytes))
        # Honour the camera orientation, then drop EXIF.
        image = ImageOps.exif_transpose(image) or image
        image.thumbnail((IMAGE_MAX_DIMENSION, IMAGE_MAX_DIMENSION), Image.Resampling.LANCZOS)
    except Image.DecompressionBombError as err:
        raise ValidationError("That image is too large to process.", code=TOO_LARGE) from err
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as err:
        raise ValidationError(
            "That file doesn't look like a valid image. Please choose a PNG, JPG or WEBP.",
            code=CONTENT_MISMATCH,
        ) from err

    image_format = PIL_FORMAT.get(extension, "PNG")
    final_extension = extension if extension in PIL_FORMAT else "png"
    out = io.BytesIO()
    if image_format == "JPEG":
        image = image.convert("RGB")  # JPEG has no alpha channel
        image.save(out, format=image_format, quality=88, optimize=True)
    else:
        image.save(out, format=image_format)
    return out.getvalue(), final_extension


def _validated(raw: bytes, *, file_name: str, category: str, definition) -> tuple[bytes, str]:
    """Run steps 1–3 of the pipeline; returns (content to store, extension)."""
    extension = _extension(file_name)
    is_image = category == FileCategoryChoices.Image.value
    limit = max_bytes(category, definition)
    if len(raw) > limit:
        raise ValidationError(
            f"That file is too large. Please keep it under {limit // MEGABYTE} MB.", code=TOO_LARGE
        )
    if not raw:
        raise ValidationError("The file is empty.", code=9005)

    accepted = ALLOWED_IMAGE_EXTS if is_image else ALLOWED_DOCUMENT_EXTS
    if definition is not None and definition.allowed_extensions:
        accepted = accepted & set(definition.allowed_extensions)
    if is_image and not extension:
        extension = "png"
    if extension not in accepted:
        shown = ", ".join(sorted(ext.upper() for ext in accepted))
        raise ValidationError(
            f"That file type isn't accepted here. Please use: {shown}.", code=TYPE_NOT_ACCEPTED
        )

    if is_image:
        return process_image(raw, extension)
    if not sniffing.matches(raw, extension):
        raise ValidationError(
            f"The file's content does not match its type (.{extension}). Please upload the original file.",
            code=CONTENT_MISMATCH,
        )
    return raw, extension


# ── Store ────────────────────────────────────────────────────────────────────
def store(
    *,
    content: bytes,
    file_name: str,
    file_type="Image",
    user=None,
    purpose: str = PURPOSE_GENERAL,
    definition_uid=None,
    entity_type: str = "",
    entity_uid=None,
    request=None,
) -> Attachment:
    """Validate, scan, store and record an upload.

    Raises :class:`ValidationError` (user-safe message) on any problem.
    """
    purpose = (purpose or PURPOSE_GENERAL).strip()
    if not policies.is_known(purpose):
        raise ValidationError(f"Unknown attachment purpose '{purpose}'.", code=9002)
    if purpose == PURPOSE_PUBLIC and PUBLISH_PUBLIC_FILES not in resolve_permissions(user, request):
        raise PermissionDeniedError("You are not allowed to publish public files.", code=NOT_ALLOWED)

    definition = selectors.get_attachment_definition(definition_uid) if definition_uid else None
    if definition is not None and not definition.is_active:
        raise ValidationError("This attachment type is no longer in use.", code=9002)
    category = definition.kind if definition is not None else _category(file_type)

    data, extension = _validated(content, file_name=file_name, category=category, definition=definition)
    scan_status = scanner.scan(data)

    now = timezone.now()
    key = f"{policies.bucket_for(purpose)}/{now:%Y}/{now:%m}/{generate_uuid7()}.{extension}"
    try:
        stored_key = default_storage.save(key, ContentFile(data))
    except Exception as err:  # noqa: BLE001 — disk full, bucket unreachable, …
        logger.exception("[attachments] could not store %s", key)
        raise ValidationError("We couldn't save your file. Please try again.", code=NOT_SAVED) from err

    attachment = Attachment(
        name=os.path.basename(file_name or f"upload.{extension}")[:255],
        storage_key=stored_key,
        path=Attachment.path_for(stored_key),
        file_type=category,
        mime_type=MIME_BY_EXT.get(extension, "application/octet-stream"),
        size=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        purpose=purpose,
        is_public=purpose == PURPOSE_PUBLIC,
        definition=definition,
        entity_type=entity_type or "",
        entity_uid=entity_uid,
        scan_status=scan_status,
    )
    if user is not None and getattr(user, "is_authenticated", False):
        attachment.created_by = user
    attachment.save()
    logger.info("[attachments] stored %s (%s, %d bytes)", stored_key, category, len(data))
    return attachment


def save_base64_attachment(*, base64_string: str, file_name: str, file_type="Image", **kwargs) -> Attachment:
    """:func:`store` for a base64-encoded upload."""
    return store(content=decode_base64(base64_string), file_name=file_name, file_type=file_type, **kwargs)


def link_to(attachment: Attachment, *, entity_type: str, entity_uid) -> Attachment:
    """Attach a stored file to the record it belongs to."""
    attachment.entity_type = entity_type
    attachment.entity_uid = entity_uid
    attachment.save(update_fields=["entity_type", "entity_uid"])
    return attachment


# ── Read ─────────────────────────────────────────────────────────────────────
def readable(attachment: Attachment, user) -> Attachment:
    """``attachment`` if ``user`` may open it; raises otherwise.

    A refusal and a missing file look the same to the caller, so the endpoint
    cannot be used to discover which files exist.
    """
    if not policies.can_read(user, attachment):
        logger.warning("[attachments] refused %s to user %s", attachment.uid, getattr(user, "pk", None))
        raise NotFoundError("We couldn't find that file. It may have been removed.")
    return attachment


def open_attachment(attachment: Attachment):
    """The stored content as a file object (the caller closes it)."""
    try:
        return default_storage.open(attachment.storage_key, "rb")
    except Exception as err:  # noqa: BLE001
        logger.error("[attachments] %s is registered but missing from storage", attachment.storage_key)
        raise NotFoundError("We couldn't find that file. It may have been removed.") from err


def read_as_base64(attachment: Attachment) -> tuple[str, str, str]:
    """Return (file_name, mime_type, base64_string) for a stored file."""
    with open_attachment(attachment) as handle:
        encoded = base64.b64encode(handle.read()).decode("utf-8")
    return attachment.name, attachment.mime_type or "application/octet-stream", encoded


def signed_url(attachment: Attachment) -> str | None:
    """A short-lived direct link, when the storage backend can issue one
    (object storage); ``None`` for local disk, which is served through the API."""
    if getattr(settings, "STORAGE_BACKEND", "local") == "local":
        return None
    try:
        return default_storage.url(attachment.storage_key)
    except Exception:  # noqa: BLE001
        return None


# ── Delete ───────────────────────────────────────────────────────────────────
def delete_attachment(*, uid, user, request=None) -> Attachment:
    """Remove a file. Its uploader may; so may a holder of "Delete uploaded files"."""
    attachment = selectors.get_attachment(uid)
    is_owner = user is not None and attachment.created_by_id == user.pk
    if not is_owner and DELETE_ATTACHMENTS not in resolve_permissions(user, request):
        raise NotFoundError("We couldn't find that file. It may have been removed.")
    attachment.delete()  # soft delete: the row stays for the trail
    try:
        default_storage.delete(attachment.storage_key)
    except Exception:  # noqa: BLE001 — the row is gone from every listing either way
        logger.warning("[attachments] could not remove %s from storage", attachment.storage_key)
    return attachment


# ════════════════════════════════════════════════════════════════════════════
# Attachment-type catalogue (configured once, reused everywhere)
# ════════════════════════════════════════════════════════════════════════════
KINDS = (FileCategoryChoices.Document.value, FileCategoryChoices.Image.value)


def _valid_kind(value) -> str:
    value = getattr(value, "value", value)
    if value in (None, ""):
        return FileCategoryChoices.Document.value
    canonical = {kind.lower(): kind for kind in KINDS}.get(str(value).strip().lower())
    if canonical is None:
        raise ValidationError(f"Invalid attachment kind: {value}. Use Document or Image.", code=9002)
    return canonical


def _norm_extensions(extensions, kind: str) -> list[str]:
    """Normalise the picked extensions and refuse any the upload path would reject.

    A definition can only promise what :func:`store` accepts for that kind;
    duplicates, dots and case are cleaned up.
    """
    allowed = selectors.allowed_extensions_for(kind)
    cleaned: list[str] = []
    for raw in extensions or []:
        extension = str(raw).strip().lower().lstrip(".")
        if not extension or extension in cleaned:
            continue
        if extension not in allowed:
            raise ValidationError(
                f"“{extension}” is not an accepted {kind.lower()} type. "
                f"Choose from: {', '.join(sorted(allowed))}.",
                code=TYPE_NOT_ACCEPTED,
            )
        cleaned.append(extension)
    return cleaned


def _unique_definition_code(name: str, *, exclude_pk=None) -> str:
    base = slugify(name)[:70] or "attachment"
    code, suffix = base, 2
    existing = AttachmentDefinition.objects.all()
    if exclude_pk is not None:
        existing = existing.exclude(pk=exclude_pk)
    while existing.filter(code=code).exists():
        code = f"{base}-{suffix}"
        suffix += 1
    return code


def _ensure_name_free(name_en: str, *, exclude_pk=None) -> None:
    taken = AttachmentDefinition.objects.filter(name_en__iexact=name_en)
    if exclude_pk is not None:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise ValidationError("An attachment type with this name already exists", code=9006)


def create_attachment_definition(
    *,
    name_en,
    name_sw="",
    description_en="",
    description_sw="",
    kind="Document",
    allowed_extensions=None,
    max_size_mb=None,
    is_active=True,
) -> AttachmentDefinition:
    name_en = (name_en or "").strip()
    if not name_en:
        raise ValidationError("An attachment name is required", code=9005)
    _ensure_name_free(name_en)
    kind = _valid_kind(kind)
    definition = AttachmentDefinition.objects.create(
        name_en=name_en,
        name_sw=name_sw or "",
        description_en=description_en or "",
        description_sw=description_sw or "",
        kind=kind,
        code=_unique_definition_code(name_en),
        allowed_extensions=_norm_extensions(allowed_extensions, kind),
        max_size_mb=max_size_mb or None,
        is_active=bool(is_active),
    )
    logger.info("[create_attachment_definition] %s (%s)", definition.name_en, definition.kind)
    return definition


def update_attachment_definition(
    *,
    uid,
    name_en=None,
    name_sw=None,
    description_en=None,
    description_sw=None,
    kind=None,
    allowed_extensions=None,
    max_size_mb=None,
    is_active=None,
) -> AttachmentDefinition:
    definition = selectors.get_attachment_definition(uid)
    if name_en is not None:
        name_en = name_en.strip()
        if not name_en:
            raise ValidationError("An attachment name cannot be empty", code=9005)
        _ensure_name_free(name_en, exclude_pk=definition.pk)
        definition.name_en = name_en
    if name_sw is not None:
        definition.name_sw = name_sw
    if description_en is not None:
        definition.description_en = description_en
    if description_sw is not None:
        definition.description_sw = description_sw
    if kind is not None:
        definition.kind = _valid_kind(kind)
    if allowed_extensions is not None:
        definition.allowed_extensions = _norm_extensions(allowed_extensions, definition.kind)
    elif kind is not None:
        # The kind changed but the types were not re-picked: they must still fit.
        definition.allowed_extensions = _norm_extensions(definition.allowed_extensions, definition.kind)
    if max_size_mb is not None:
        definition.max_size_mb = max_size_mb or None
    if is_active is not None:
        definition.is_active = bool(is_active)
    definition.save()
    logger.info("[update_attachment_definition] %s", definition.name_en)
    return definition


def delete_attachment_definition(*, uid) -> AttachmentDefinition:
    definition = selectors.get_attachment_definition(uid)
    if definition.attachments.filter(deleted=False).exists():
        raise ValidationError(
            "Files have been uploaded for this attachment type, so it cannot be deleted; "
            "deactivate it instead.",
            code=9007,
        )
    definition.delete()  # soft delete
    logger.info("[delete_attachment_definition] %s", definition.name_en)
    return definition
