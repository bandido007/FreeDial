import asyncio

from django.core.management.base import BaseCommand, CommandError

from brain import service
from brain.adapters.config import BrainSettings


class Command(BaseCommand):
    help = "Run the brain, AlgoDial's call control (ARI + Redis Streams)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check", action="store_true", help="Check ARI and Redis are reachable, then exit."
        )
        parser.add_argument(
            "--record",
            metavar="FILE",
            help="Append every decision (input, ids, actions) to FILE as JSON lines, for replay tests.",
        )

    def handle(self, *args, **options):
        settings = BrainSettings.from_django()

        if options["check"]:
            results = asyncio.run(service.check(settings))
            for result in results:
                self.stdout.write(f"{result.name:<6} {'ok' if result.ok else 'FAIL':<4}  {result.detail}")
            failed = [result.name for result in results if not result.ok]
            if failed:
                raise CommandError(f"cannot reach {', '.join(failed)}")
            return

        record = open(options["record"], "a") if options["record"] else None  # noqa: SIM115
        try:
            asyncio.run(service.run(settings, record=record))
        except service.StartupError as exc:
            raise CommandError(str(exc)) from exc
        finally:
            if record is not None:
                record.close()
