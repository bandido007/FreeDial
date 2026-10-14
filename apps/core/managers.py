"""Reusable model managers and querysets."""

from django.db import models


class SoftDeleteQuerySet(models.QuerySet):
    """QuerySet that understands the soft-delete flag."""

    def alive(self):
        return self.filter(deleted=False)

    def dead(self):
        return self.filter(deleted=True)

    def active(self):
        return self.filter(deleted=False, active=True)


class ActiveManager(models.Manager):
    """Default manager that hides soft-deleted rows.

    Use ``Model.all_objects`` (a plain manager) when you genuinely need to see
    deleted rows — e.g. audit/restore flows.
    """

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(deleted=False)

    def active(self):
        return self.get_queryset().filter(active=True)
