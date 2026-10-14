from django.core.management.base import BaseCommand

from apps.realtime import relay


class Command(BaseCommand):
    help = "Push brain events to the agent pages' WebSockets (the live relay)."

    def handle(self, *args, **options):
        try:
            relay.run()
        except KeyboardInterrupt:
            pass
