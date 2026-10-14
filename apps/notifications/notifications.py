"""Categories the notifications app itself sends (see catalogue.py)."""

from apps.notifications.catalogue import Category

GENERIC = "GENERIC"

CATEGORIES = [
    Category(
        code=GENERIC,
        name="General message",
        description="A message with no more specific category, including gateway test messages.",
    ),
]
