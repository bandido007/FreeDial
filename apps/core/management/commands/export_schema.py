"""Write the GraphQL schema to ``docs/schema.graphql`` — or check it is current.

    python manage.py export_schema          # (re)write the snapshot
    python manage.py export_schema --check  # exit 1 if it is out of date

The snapshot is what frontend code generators read, and its diff in a merge
request is the API change review: a renamed field or a dropped operation shows
up as a line, not as a surprise in production. ``make schema`` writes it; the
guard test in ``tests/test_api_contract.py`` fails when it is stale.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from graphql import print_schema

SCHEMA_PATH = Path(settings.BASE_DIR) / "docs" / "schema.graphql"


def render_schema() -> str:
    from config.schema import schema

    return print_schema(schema.graphql_schema).rstrip() + "\n"


class Command(BaseCommand):
    help = "Write docs/schema.graphql from the live GraphQL schema."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Don't write; fail if the committed snapshot differs from the live schema.",
        )

    def handle(self, *args, **options):
        rendered = render_schema()
        current = SCHEMA_PATH.read_text() if SCHEMA_PATH.exists() else ""
        if options["check"]:
            if current != rendered:
                raise CommandError(
                    "docs/schema.graphql is out of date — run `make schema` and commit the result."
                )
            self.stdout.write(self.style.SUCCESS("The schema snapshot is up to date."))
            return
        SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCHEMA_PATH.write_text(rendered)
        self.stdout.write(self.style.SUCCESS(f"Wrote {SCHEMA_PATH}"))
