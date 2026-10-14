import asyncio
import logging
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from aiworker.service import run_worker


class Command(BaseCommand):
    help = "The AI agent's voice worker (M8): talks with callers on entry points that have an AI agent."

    def handle(self, *args, **options):
        key = settings.OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise CommandError("OPENAI_API_KEY is not set (.env). Calls to AI entry points go to people.")
        logging.getLogger("aiworker").setLevel(logging.INFO)
        try:
            asyncio.run(run_worker(key))
        except KeyboardInterrupt:
            pass
