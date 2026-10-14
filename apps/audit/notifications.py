"""Notification categories the audit app sends."""

from apps.notifications.catalogue import Category

ERROR_DIGEST = "ERROR_DIGEST"

CATEGORIES = [
    Category(
        code=ERROR_DIGEST,
        name="Daily error report",
        description="A morning summary of the requests that failed unexpectedly the day before.",
    ),
]
