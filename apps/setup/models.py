"""The setup app keeps one thing of its own: a person's word that a hands-on test works.

Everything else the checklist shows is read from the configuration and the call records
(``apps.setup.checklist``), so it can never be out of date.
"""

from django.db import models

from apps.core.models import BaseModel


class SetupConfirmation(BaseModel):
    """Someone tried one hands-on test of the checklist and it worked. Who and when: ``created_by``
    and ``created_at``. Clearing it soft-deletes the row, so the history stays."""

    test = models.CharField(max_length=60, help_text="Which hands-on test, e.g. confirm.audio")
    note = models.CharField(max_length=300, blank=True, help_text="What was tried, in a few words")

    class Meta:
        db_table = "setup_confirmations"
        ordering = ["-created_at"]
        verbose_name = "setup guide"
        verbose_name_plural = "Setup guide and checklist"

    def __str__(self):
        return f"{self.test} confirmed {self.created_at:%Y-%m-%d %H:%M}"
