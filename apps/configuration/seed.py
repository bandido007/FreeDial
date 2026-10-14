"""Seed step for `manage.py seed`: check the declared settings."""

from apps.configuration import catalogue

ORDER = 15


def run(command) -> None:
    issues = catalogue.problems()
    if issues:
        raise RuntimeError("The settings catalogue is invalid:\n- " + "\n- ".join(issues))
    command.stdout.write(f"  • {len(catalogue.specs())} runtime settings declared")
