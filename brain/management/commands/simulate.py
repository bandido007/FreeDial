import random

from django.core.management.base import BaseCommand

from brain.core.config import QueueConfig, SkillRule, Strategy
from brain.sim.simulator import Simulation, VirtualAgent, VirtualCall


class Command(BaseCommand):
    help = (
        "Run a virtual busy hour through the real brain core (no Asterisk, no waiting) and compare "
        "queue strategies: who took the calls, waits, service level."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--strategy", choices=[s.value for s in Strategy], help="Only this one (default: all)"
        )
        parser.add_argument("--agents", type=int, default=5)
        parser.add_argument("--calls", type=int, default=90)
        parser.add_argument("--every", type=float, default=40, help="Seconds between callers, on average")
        parser.add_argument("--talk", type=float, default=150, help="Average conversation, seconds")
        parser.add_argument("--target", type=int, default=20, help="Service-level target, seconds")
        parser.add_argument(
            "--skills", action="store_true", help="Swahili rule set: experts first, widening at 30 s and 60 s"
        )
        parser.add_argument("--seed", type=int, default=1)

    def handle(self, *args, **options):
        rng = random.Random(options["seed"])  # noqa: S311 (simulated traffic, not security)
        calls, at = [], 0.0
        for _ in range(options["calls"]):
            at += rng.expovariate(1 / options["every"])
            calls.append(
                VirtualCall(at=at, talk=rng.expovariate(1 / options["talk"]), patience=rng.uniform(30, 300))
            )
        agents = [
            VirtualAgent(f"70{n:02d}", skills={"swahili": rng.randint(1, 10)}, answer_after=rng.uniform(1, 6))
            for n in range(1, options["agents"] + 1)
        ]
        rules = (SkillRule(0, "swahili >= 8"), SkillRule(30, "swahili >= 5"), SkillRule(60, "true"))
        strategies = [options["strategy"]] if options["strategy"] else [s.value for s in Strategy]
        if options["skills"]:
            levels = ", ".join(f"{agent.number}={agent.skills['swahili']}" for agent in agents)
            self.stdout.write(f"swahili levels: {levels}; rules: 0 s >= 8, 30 s >= 5, 60 s anyone\n")
        for strategy in strategies:
            queue = QueueConfig(
                uid="support",
                name="Support",
                strategy=strategy,
                ring_timeout=15,
                wrapup_seconds=10,
                skill_rules=rules if options["skills"] else (),
            )
            report = Simulation(queues=[queue], agents=agents, calls=calls).run()
            self.stdout.write(f"── {strategy}")
            self.stdout.write(report.table(service_seconds=options["target"]) + "\n")
