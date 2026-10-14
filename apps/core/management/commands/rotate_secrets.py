"""Re-encrypt every stored secret under the current primary encryption key.

    python manage.py rotate_secrets

The rotation procedure (see ``apps/core/crypto.py``):

1. Prepend the new key to ``ENCRYPTION_KEYS`` and restart (new writes use it;
   old values still decrypt with the old key).
2. Run this command: every ``*_encrypted`` column is re-encrypted under the
   new key. Values that cannot be read are left untouched and reported.
3. Remove the old key from ``ENCRYPTION_KEYS``.

Any model that stores a secret is found automatically: a column whose name
ends in ``_encrypted`` on any installed model — so a business app that adds
one needs no registration here.
"""

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.crypto import rotate_secret

SUFFIX = "_encrypted"


def encrypted_columns():
    """``(model, [field names])`` for every model holding an encrypted column."""
    for model in apps.get_models():
        names = [field.name for field in model._meta.concrete_fields if field.name.endswith(SUFFIX)]
        if names:
            yield model, names


class Command(BaseCommand):
    help = "Re-encrypt every stored secret under the first ENCRYPTION_KEYS entry."

    @transaction.atomic
    def handle(self, *args, **options):
        rotated = unchanged = 0
        for model, names in encrypted_columns():
            manager = getattr(model, "all_objects", model._default_manager)
            for row in manager.all().only("pk", *names).iterator():
                changed = []
                for name in names:
                    current = getattr(row, name) or ""
                    if not current:
                        continue
                    fresh = rotate_secret(current)
                    if fresh == current:
                        unchanged += 1
                        continue
                    setattr(row, name, fresh)
                    changed.append(name)
                if changed:
                    manager.filter(pk=row.pk).update(**{name: getattr(row, name) for name in changed})
                    rotated += len(changed)
            self.stdout.write(f"  • {model._meta.label}: {', '.join(names)}")
        self.stdout.write(self.style.SUCCESS(f"Rotated {rotated} secret(s); {unchanged} left as they were."))
        if unchanged:
            self.stdout.write(
                self.style.WARNING(
                    "A value is left unchanged when it is already under the primary key or could not be "
                    "decrypted with any configured key — check the log before dropping the old key."
                )
            )
