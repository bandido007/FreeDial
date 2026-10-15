import asyncio
import os
from pathlib import Path

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

        others = another_brain()
        if others:
            # Two brains on one ARI app split the commands between them: one takes an agent's login,
            # the other refuses that agent's Ready (seen on 15 October 2026 with two `make dev`).
            raise CommandError(
                f"another brain is already running on this machine (pid {', '.join(others)}). "
                "Stop it first (Ctrl-C in its terminal)."
            )
        record = open(options["record"], "a") if options["record"] else None  # noqa: SIM115
        try:
            asyncio.run(service.run(settings, record=record))
        except service.StartupError as exc:
            raise CommandError(str(exc)) from exc
        finally:
            if record is not None:
                record.close()


def another_brain() -> list[str]:
    """Other brain processes on this machine: argv ``... manage.py run_brain ...`` (not a shell that
    merely mentions it, like the one `make dev` runs, and not this process)."""
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or proc.name == str(os.getpid()):
            continue
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if any(a.endswith(b"manage.py") and b == b"run_brain" for a, b in zip(argv, argv[1:], strict=False)):
            found.append(proc.name)
    return found
