"""Where the brain's configuration comes from.

The ORM is used here and only here, and only when loading: at start and when a
``config.changed`` command arrives. Never while handling a call event. The
service runs :func:`load_config` in a thread, since the ORM is synchronous.
"""

from dataclasses import dataclass

from django.conf import settings
from django.db import close_old_connections, connections

from apps.agents.models import Agent, AgentSkill, Skill
from apps.queues.models import EntryPoint, Queue, QueueMember, RuleRequirement
from brain.core.config import AgentConfig, Config, EntryConfig, QueueConfig, Requirement, Step


@dataclass(frozen=True)
class BrainSettings:
    ari_url: str
    ari_user: str
    ari_password: str
    ari_app: str
    bus_redis_url: str
    commands_stream: str
    events_stream: str
    agents_key: str = "algodial:agents"
    consumer: str = "brain-1"

    @classmethod
    def from_django(cls) -> "BrainSettings":
        return cls(
            ari_url=settings.ARI_URL,
            ari_user=settings.ARI_USER,
            ari_password=settings.ARI_PASSWORD,
            ari_app=settings.ARI_APP,
            bus_redis_url=settings.BUS_REDIS_URL,
            commands_stream=settings.BUS_COMMANDS_STREAM,
            events_stream=settings.BUS_EVENTS_STREAM,
            agents_key=settings.BUS_AGENTS_KEY,
            consumer=settings.BRAIN_CONSUMER,
        )


def load_config() -> Config:
    """Active, not deleted agents (with skills), queues (with members and skill rules) and entry points."""
    close_old_connections()
    try:
        levels: dict[int, dict[str, int]] = {}
        for row in AgentSkill.objects.filter(skill__active=True, skill__deleted=False).select_related(
            "skill"
        ):
            levels.setdefault(row.agent_id, {})[row.skill.name] = row.level
        agents = {
            str(agent.uid): AgentConfig(
                uid=str(agent.uid), number=agent.number, name=agent.name, skills=levels.get(agent.pk, {})
            )
            for agent in Agent.objects.filter(active=True)
        }
        members: dict[int, list[str]] = {}
        for member in QueueMember.objects.select_related("agent").order_by("priority", "agent__number"):
            if str(member.agent.uid) in agents:
                members.setdefault(member.queue_id, []).append(str(member.agent.uid))
        grouped: dict[int, dict[int | None, list[Requirement]]] = {}
        rows = RuleRequirement.objects.filter(rule_set__active=True, rule_set__deleted=False).order_by(
            "skill"
        )
        for need in rows:
            step = grouped.setdefault(need.rule_set_id, {}).setdefault(need.until_seconds, [])
            step.append(Requirement(need.skill, need.min_level))
        rules = {
            rule_set: tuple(
                Step(until, tuple(needs))
                for until, needs in sorted(steps.items(), key=lambda step: (step[0] is None, step[0] or 0))
            )
            for rule_set, steps in grouped.items()
        }
        live = {queue.pk: queue for queue in Queue.objects.filter(active=True)}
        queues = {
            str(queue.uid): QueueConfig(
                uid=str(queue.uid),
                name=queue.name,
                ring_timeout=queue.ring_timeout,
                max_wait=queue.max_wait,
                retry_delay=queue.retry_delay,
                moh_class=queue.moh_class,
                wrapup_seconds=queue.wrapup_seconds,
                members=tuple(members.get(queue.pk, ())),
                strategy=queue.strategy,
                max_callers=queue.max_callers,
                priority=queue.priority,
                overflow_queue=str(live[queue.overflow_queue_id].uid)
                if queue.overflow_queue_id in live
                else None,
                overflow_action=queue.overflow_action,
                steps=rules.get(queue.skill_rule_set_id, ()) if queue.skill_rule_set_id else (),
            )
            for queue in live.values()
        }
        entries = {
            entry.number: EntryConfig(
                number=entry.number,
                queue=str(entry.queue.uid),
                language=entry.language.name if entry.language else None,
                priority=entry.priority,
                fallback_queue=str(live[entry.fallback_queue_id].uid)
                if entry.fallback_queue_id in live
                else None,
            )
            for entry in EntryPoint.objects.filter(active=True).select_related("queue", "language")
            if str(entry.queue.uid) in queues
        }
        skills = dict(Skill.objects.filter(active=True).values_list("name", "kind"))
        return Config(
            agents=agents,
            queues=queues,
            entries=entries,
            skills=skills,
            dial_template=settings.TRANSFER_DIAL_TEMPLATE,
        )
    finally:
        connections.close_all()  # loads are rare: hold no idle connection in between
