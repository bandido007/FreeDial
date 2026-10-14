"""Where the brain's configuration comes from.

The ORM is used here and only here, and only when loading: at start and when a
``config.changed`` command arrives. Never while handling a call event. The
service runs :func:`load_config` in a thread, since the ORM is synchronous.
"""

from dataclasses import dataclass

from django.conf import settings
from django.db import close_old_connections, connections

from apps.agents.models import Agent
from apps.queues.models import EntryPoint, Queue, QueueMember
from brain.core.config import AgentConfig, Config, QueueConfig


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
    """Active, not deleted agents, queues and entry points, as the core's plain data."""
    close_old_connections()
    try:
        agents = {
            str(agent.uid): AgentConfig(uid=str(agent.uid), number=agent.number, name=agent.name)
            for agent in Agent.objects.filter(active=True)
        }
        members: dict[int, list[str]] = {}
        for member in QueueMember.objects.select_related("agent").order_by("priority", "agent__number"):
            if str(member.agent.uid) in agents:
                members.setdefault(member.queue_id, []).append(str(member.agent.uid))
        queues = {
            str(queue.uid): QueueConfig(
                uid=str(queue.uid),
                name=queue.name,
                ring_timeout=queue.ring_timeout,
                max_wait=queue.max_wait,
                retry_delay=queue.retry_delay,
                moh_class=queue.moh_class,
                members=tuple(members.get(queue.pk, ())),
            )
            for queue in Queue.objects.filter(active=True)
        }
        entries = {
            entry.number: str(entry.queue.uid)
            for entry in EntryPoint.objects.filter(active=True).select_related("queue")
            if str(entry.queue.uid) in queues
        }
        return Config(agents=agents, queues=queues, entries=entries)
    finally:
        connections.close_all()  # loads are rare: hold no idle connection in between
