"""What a supervisor may see and act on: the agents of the teams they supervise, and those agents' queues."""

from dataclasses import dataclass, field
from typing import Any

from apps.agents.models import Agent
from apps.queues.models import QueueMember
from apps.teams.models import Team


@dataclass(frozen=True)
class Scope:
    everything: bool = False  # a superuser sees every agent and call
    agents: frozenset[str] = field(default_factory=frozenset)  # agent uids
    queues: frozenset[str] = field(default_factory=frozenset)  # queue names

    def has_agent(self, uid: str | None) -> bool:
        return bool(uid) and (self.everything or uid in self.agents)

    def has_call(self, snapshot: dict[str, Any] | None) -> bool:
        if not snapshot:
            return False
        if self.everything or snapshot.get("queue") in self.queues:
            return True
        return self.has_agent(snapshot.get("agent")) or any(
            uid in self.agents for uid in snapshot.get("ringing") or []
        )

    def has_event(self, data: dict[str, Any]) -> bool:
        return (
            self.everything
            or self.has_agent(data.get("agent"))
            or self.has_agent(data.get("target_agent"))
            or data.get("queue") in self.queues
        )


def scope_of(user) -> Scope:
    if user.is_superuser:
        return Scope(everything=True)
    members = Team.objects.filter(supervisors=user).values_list("agents", flat=True)
    agents = Agent.objects.filter(user__in=[pk for pk in members if pk], active=True)
    queues = QueueMember.objects.filter(agent__in=agents, queue__active=True).values_list(
        "queue__name", flat=True
    )
    return Scope(agents=frozenset(str(agent.uid) for agent in agents), queues=frozenset(queues))
