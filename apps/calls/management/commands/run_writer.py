import socket

from django.core.management.base import BaseCommand

from apps.calls import writer


class Command(BaseCommand):
    help = "Write brain events from the events stream into Event, Call and Leg."

    def add_arguments(self, parser):
        parser.add_argument(
            "--consumer",
            default=f"writer-{socket.gethostname()}",
            help="Consumer name in the 'writer' group. Keep it stable so a restart picks up its own pending entries.",
        )

    def handle(self, *args, **options):
        try:
            writer.run(options["consumer"])
        except KeyboardInterrupt:
            pass
