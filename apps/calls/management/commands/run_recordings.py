import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.calls import recordings


class Command(BaseCommand):
    help = (
        "Keep each ended call's stereo recording (M7): fetch Asterisk's calls/<linkedid>.raw over ARI, "
        "save it as a WAV, delete Asterisk's copy. Loops; safe to run twice."
    )

    def add_arguments(self, parser):
        parser.add_argument("--every", type=float, default=2.0, help="Seconds between rounds.")
        parser.add_argument("--once", action="store_true", help="One round, then exit.")

    def handle(self, *args, **options):
        try:
            while True:
                close_old_connections()
                kept = recordings.convert_pending()
                if kept:
                    self.stdout.write(f"kept {kept} recording(s)")
                if options["once"]:
                    return
                time.sleep(options["every"])
        except KeyboardInterrupt:
            pass
