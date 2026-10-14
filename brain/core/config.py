"""The configuration the core decides with: agents, queues and entry points.

Plain frozen data. ``brain.adapters.config`` builds it from the database at start
and on every ``config.changed`` command; the core never reads the database.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentConfig:
    uid: str
    number: str
    name: str


@dataclass(frozen=True)
class QueueConfig:
    uid: str
    name: str
    ring_timeout: int = 20  # seconds an agent's phone rings before the next agent
    max_wait: int = 300  # seconds a caller waits before overflow
    retry_delay: int = 5  # seconds before agents who rejected this call are tried again
    moh_class: str = "default"
    wrapup_seconds: int = 0  # after-call work before the next offer; 0 = none
    members: tuple[str, ...] = ()  # agent uids


@dataclass(frozen=True)
class Config:
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    queues: dict[str, QueueConfig] = field(default_factory=dict)
    entries: dict[str, str] = field(default_factory=dict)  # number dialled -> queue uid
