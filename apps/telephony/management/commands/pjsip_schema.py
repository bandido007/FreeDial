from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.telephony import asterisk_schema, services


class Command(BaseCommand):
    help = (
        "Create or update Asterisk's PJSIP realtime tables with Asterisk's own Alembic, "
        "grant Asterisk's read-only role, and rewrite every live SIP account's rows."
    )

    def handle(self, *args, **options):
        try:
            before = asterisk_schema.current_revision()
            asterisk_schema.upgrade()
            after = asterisk_schema.current_revision()
        except asterisk_schema.AsteriskSchemaError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Asterisk realtime schema: {before or 'none'} -> {after}")

        if settings.ASTERISK_DB_PASSWORD:
            asterisk_schema.grant_asterisk_role(
                user=settings.ASTERISK_DB_USER, password=settings.ASTERISK_DB_PASSWORD
            )
            tables = ", ".join(asterisk_schema.ASTERISK_TABLES)
            self.stdout.write(f"Role {settings.ASTERISK_DB_USER!r} may read {tables}")
        else:
            self.stdout.write(
                self.style.WARNING("ASTERISK_DB_PASSWORD is empty: Asterisk's database role was not set")
            )

        self.stdout.write(f"SIP accounts written to the PJSIP tables: {services.resync_pjsip()}")
