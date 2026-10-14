"""Attachment metadata — one row per stored file — and the catalogue of
attachment types.

Files live in the configured storage (local disk by default, S3 / MinIO by
configuration); ``storage_key`` is where. ``path`` is the media-relative URL
the client keeps (for example on ``user.photo``) and hands back to open the
file — it is an *address*, never a permission: every read goes through the
access policy of the file's ``purpose`` (see ``policies.py``).
"""

from django.conf import settings
from django.db import models

from apps.attachments.constants import PURPOSE_GENERAL, FileCategoryChoices, ScanStatusChoices
from apps.core.models import BaseModel


class AttachmentDefinition(BaseModel):
    """A reusable, configurable attachment *type*.

    Instead of re-defining a required document inline wherever one is asked
    for, an administrator sets it up once here — its name, kind (Document /
    Image), accepted file types and size cap — and any module can then require
    it by reference.
    """

    code = models.SlugField(max_length=80, help_text="Stable machine key, e.g. curriculum-vitae.")
    name_en = models.CharField(max_length=255, help_text='e.g. "Curriculum Vitae (CV)"')
    name_sw = models.CharField(max_length=255, blank=True)
    description_en = models.CharField(max_length=600, blank=True)
    description_sw = models.CharField(max_length=600, blank=True)
    kind = models.CharField(
        max_length=20,
        choices=FileCategoryChoices.choices(),
        default=FileCategoryChoices.Document.value,
        help_text="Document (CV, letter, …) or Image (e.g. a passport-size photo).",
    )
    allowed_extensions = models.JSONField(
        default=list, blank=True, help_text='e.g. ["pdf","jpg"]; empty = any the category allows.'
    )
    max_size_mb = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "attachment_definitions"
        ordering = ["name_en"]
        verbose_name = "Attachment definition"
        verbose_name_plural = "Attachment definitions"
        constraints = [
            models.UniqueConstraint(
                fields=["name_en"],
                condition=models.Q(deleted=False),
                name="unique_active_attachment_name",
            ),
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(deleted=False),
                name="unique_active_attachment_code",
            ),
        ]

    def __str__(self):
        return f"{self.name_en} ({self.kind})"


class Attachment(BaseModel):
    """A single uploaded file. ``created_by`` (from BaseModel) is the uploader."""

    name = models.CharField(max_length=255, help_text="Original file name.")
    storage_key = models.CharField(
        max_length=500, unique=True, help_text="Where the file lives in the storage backend."
    )
    path = models.CharField(
        max_length=1000,
        db_index=True,
        help_text="Media-relative URL, e.g. /media/profiles/2026/07/<uid>.jpg",
    )
    file_type = models.CharField(
        max_length=20,
        choices=FileCategoryChoices.choices(),
        default=FileCategoryChoices.Image.value,
        db_index=True,
    )
    mime_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0, help_text="Size in bytes.")
    checksum = models.CharField(max_length=64, blank=True, db_index=True, help_text="SHA-256 of the content.")

    # What the file is for — decides who may read it.
    purpose = models.CharField(max_length=60, default=PURPOSE_GENERAL, db_index=True)
    is_public = models.BooleanField(default=False, help_text="Readable without signing in.")
    definition = models.ForeignKey(
        AttachmentDefinition,
        related_name="attachments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # The record the file belongs to, as a loose reference: the owning app
    # resolves it, so this table needs no foreign key to every other table.
    entity_type = models.CharField(max_length=100, blank=True, db_index=True)
    entity_uid = models.UUIDField(null=True, blank=True, db_index=True)

    scan_status = models.CharField(
        max_length=10,
        choices=ScanStatusChoices.choices(),
        default=ScanStatusChoices.SKIPPED.value,
    )

    class Meta:
        db_table = "attachments"
        ordering = ["-created_at"]
        verbose_name = "Attachment"
        verbose_name_plural = "Attachments"
        indexes = [
            models.Index(fields=["file_type", "created_at"]),
            models.Index(fields=["entity_type", "entity_uid"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.file_type})"

    @property
    def owner_id(self):
        return self.created_by_id

    @property
    def extension(self) -> str:
        return self.storage_key.rsplit(".", 1)[-1].lower() if "." in self.storage_key else ""

    @staticmethod
    def path_for(storage_key: str) -> str:
        return f"{settings.MEDIA_URL}{storage_key}"
