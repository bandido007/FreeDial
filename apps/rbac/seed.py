"""Seed step for `manage.py seed` (see apps.core.management.commands.seed)."""

from django.core.management import call_command

ORDER = 10


def run(command) -> None:
    call_command("sync_rbac", stdout=command.stdout, stderr=command.stderr)
