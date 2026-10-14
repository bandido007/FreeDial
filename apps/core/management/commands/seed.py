"""Run every app's seed step, in order.

    python manage.py seed

An app that has reference data to install ships a ``seed.py``::

    # apps/<app>/seed.py
    ORDER = 50                      # lower runs first; foundation uses 10–40

    def run(command) -> None:       # `command` is this management command
        command.stdout.write("Seeding invoices…")
        ...

Every step must be **idempotent** — the container entrypoint runs ``seed`` on
each start.
"""

from django.core.management.base import BaseCommand

from apps.core.registry import app_modules

DEFAULT_ORDER = 100


class Command(BaseCommand):
    help = "Run every installed app's seed step (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--only", nargs="*", default=[], help="App labels to seed (default: all).")

    def handle(self, *args, **options):
        only = set(options["only"] or [])
        steps = sorted(
            (
                (getattr(module, "ORDER", DEFAULT_ORDER), app_config.label, module)
                for app_config, module in app_modules("seed")
                if callable(getattr(module, "run", None))
            ),
            key=lambda step: (step[0], step[1]),
        )
        ran = 0
        for _order, label, module in steps:
            if only and label not in only:
                continue
            self.stdout.write(self.style.MIGRATE_HEADING(f"[{label}]"))
            module.run(self)
            ran += 1
        self.stdout.write(self.style.SUCCESS(f"Seed complete ({ran} step(s))."))
