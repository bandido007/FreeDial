"""Runtime settings an administrator may change (see apps.core.catalogue).

Only **overrides** are stored. A setting nobody has changed has no row; its
value is the Django setting of the same name, else the declared default. That
keeps the table small, makes "reset to default" a delete, and means a new
release can change a default without fighting stored copies of the old one.
"""

from django.db import models

from apps.core.models import BaseModel


class SystemSetting(BaseModel):
    """The saved value of one runtime setting."""

    key = models.CharField(max_length=100, db_index=True)
    value = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "system_settings"
        ordering = ["key"]
        verbose_name = "System setting"
        verbose_name_plural = "System settings"
        constraints = [
            models.UniqueConstraint(
                fields=["key"], condition=models.Q(deleted=False), name="unique_active_system_setting"
            )
        ]

    def __str__(self):
        return f"{self.key} = {self.value!r}"
