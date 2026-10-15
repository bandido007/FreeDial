"""Writes of the setup app: confirm that a hands-on test works, or clear that mark."""

import logging

from django.db import transaction

from apps.core.exceptions import ValidationError
from apps.setup.checklist import HANDS_ON
from apps.setup.models import SetupConfirmation

logger = logging.getLogger(__name__)

UNKNOWN_CHECK = 10500


def _known(check) -> str:
    if check not in HANDS_ON:
        raise ValidationError("That is not one of the checklist's hands-on tests.", code=UNKNOWN_CHECK)
    return check


@transaction.atomic
def confirm(*, actor, check, note="") -> SetupConfirmation:
    """``actor`` tried the test and it worked. Replaces an earlier confirmation of the same test."""
    check = _known(check)
    for old in SetupConfirmation.objects.filter(test=check):
        old.delete()
    row = SetupConfirmation.objects.create(
        test=check, note=" ".join((note or "").split())[:300], created_by=actor
    )
    logger.info("[setup] %s confirmed by %s", check, getattr(actor, "pk", None))
    return row


@transaction.atomic
def clear(*, actor, check) -> None:
    """The test needs doing again (something changed, or it did not work after all)."""
    for row in SetupConfirmation.objects.filter(test=_known(check)):
        row.delete()
    logger.info("[setup] %s cleared by %s", check, getattr(actor, "pk", None))
