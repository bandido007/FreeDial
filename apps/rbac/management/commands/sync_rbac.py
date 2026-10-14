"""Write the permission catalogue to the database.

    python manage.py sync_rbac            # apply
    python manage.py sync_rbac --check    # report problems, change nothing

Idempotent and safe on every deploy: it collects each app's ``permissions.py``,
creates or updates the permissions, retires the ones no app declares any more,
keeps the system role complete, creates the declared roles, and (when
``DEFAULT_ADMIN_EMAIL`` / ``DEFAULT_ADMIN_PASSWORD`` are set) makes sure the
first administrator exists.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.exceptions import ApplicationError
from apps.rbac import catalogue, services


class Command(BaseCommand):
    help = "Sync permissions, roles and the bootstrap administrator from the declared catalogue."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Validate the catalogue and exit without writing anything.",
        )

    def handle(self, *args, **options):
        if options["check"]:
            issues = catalogue.problems()
            if issues:
                raise CommandError("The permission catalogue is invalid:\n- " + "\n- ".join(issues))
            declared = catalogue.collect()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Catalogue is valid: {len(declared.codes())} permissions, {len(declared.roles)} roles."
                )
            )
            return

        self.stdout.write("Syncing roles and permissions…")
        try:
            services.sync_catalogue(log=self.stdout.write)
            services.ensure_bootstrap_admin(
                email=settings.DEFAULT_ADMIN_EMAIL,
                password=settings.DEFAULT_ADMIN_PASSWORD,
                first_name=settings.DEFAULT_ADMIN_FIRST_NAME,
                last_name=settings.DEFAULT_ADMIN_LAST_NAME,
                log=self.stdout.write,
            )
        except ApplicationError as exc:
            raise CommandError(exc.message) from exc
        self.stdout.write(self.style.SUCCESS("Roles and permissions are in sync."))
