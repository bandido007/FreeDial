"""Abstract base models composed from small, single-responsibility mixins.

Rather than one monolithic base (and copy-pasting the same fields onto every
model and onto the custom User), behaviour is split into mixins that can be
combined as needed:

* :class:`UUIDModel`        — public, time-ordered ``uid`` (UUIDv7).
* :class:`TimeStampedModel` — ``created_at`` / ``updated_at``.
* :class:`AuditUserModel`   — ``created_by`` / ``updated_by`` / ``deleted_by`` /
                              ``deactivated_by`` (auto-stamped from the acting
                              user — see :mod:`apps.core.context`).
* :class:`SoftDeleteModel`  — ``active`` / ``deleted`` flags + soft ``delete()``.

:class:`BaseModel` combines all four and is the default base for domain models.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.context import get_current_user
from apps.core.managers import ActiveManager
from apps.core.utils import generate_uuid7


class UUIDModel(models.Model):
    """Adds a stable, externally-exposed UUIDv7 identifier.

    The integer ``pk`` stays the internal key (compact joins/indexes); ``uid``
    is what the API and the frontend reference.
    """

    uid = models.UUIDField(
        default=generate_uuid7,
        unique=True,
        editable=False,
        db_index=True,
        verbose_name="Public ID",
    )

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class AuditUserModel(models.Model):
    """Tracks which user created / updated / deleted / deactivated a row.

    ``save()`` stamps ``created_by`` (on insert) and ``updated_by`` (always)
    from the acting user: the signed-in user during a request, or whoever a
    task named with ``acting_as(user)``.

    A partial save (``save(update_fields=[...])``) still records who and when:
    ``updated_by`` and ``updated_at`` are added to the field list, which Django
    would otherwise leave untouched.
    """

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="%(class)s_created",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="%(class)s_updated",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="%(class)s_deleted",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    deactivated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="%(class)s_deactivated",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        user = get_current_user()
        if user is not None and getattr(user, "is_authenticated", False):
            if self._state.adding and self.created_by_id is None:
                self.created_by = user
            self.updated_by = user

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            stamped = {"updated_by", "updated_at"} & {f.name for f in self._meta.concrete_fields}
            kwargs["update_fields"] = list(set(update_fields) | stamped)
        super().save(*args, **kwargs)


class SoftDeleteModel(models.Model):
    """Soft delete + (de)activation, with a deleted-row-hiding default manager."""

    active = models.BooleanField(default=True)
    deleted = models.BooleanField(default=False)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # Default manager hides soft-deleted rows; all_objects sees everything.
    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        """Soft delete — flag the row and stamp who/when, keep the record."""
        self.deleted = True
        self.deleted_at = timezone.now()
        user = get_current_user()
        if user is not None and getattr(user, "is_authenticated", False):
            self.deleted_by = user
        self.save(update_fields=None)

    def restore(self):
        """Undo a soft delete."""
        self.deleted = False
        self.deleted_at = None
        self.deleted_by = None
        self.save()

    def hard_delete(self, using=None, keep_parents=False):
        """Permanently remove the row (bypasses soft delete)."""
        super().delete(using=using, keep_parents=keep_parents)

    def deactivate(self):
        self.active = False
        self.deactivated_at = timezone.now()
        user = get_current_user()
        if user is not None and getattr(user, "is_authenticated", False):
            self.deactivated_by = user
        self.save()

    def activate(self):
        self.active = True
        self.deactivated_at = None
        self.deactivated_by = None
        self.save()


class BaseModel(UUIDModel, TimeStampedModel, AuditUserModel, SoftDeleteModel):
    """Standard base for domain models: UUID + timestamps + audit + soft delete."""

    class Meta:
        abstract = True
        ordering = ["-created_at"]
