"""The configuration the core decides with: agents, queues and entry points.

Plain frozen data. ``brain.adapters.config`` builds it from the database at start
and on every ``config.changed`` command; the core never reads the database.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class Strategy(StrEnum):
    """Which free agent(s) a queue rings."""

    LONGEST_IDLE = "longest_idle"  # the agent idle the longest
    RING_ALL = "ring_all"  # every free agent at once; the first to answer takes it
    ROUND_ROBIN = "round_robin"  # the next member after the one rung last, in member order
    LINEAR = "linear"  # always the first free member in member order
    FEWEST_CALLS = "fewest_calls"  # the agent who answered the fewest calls of this queue
    RANDOM = "random"


@dataclass(frozen=True)
class AgentConfig:
    uid: str
    number: str
    name: str
    skills: dict[str, int] = field(default_factory=dict)  # skill name -> level 1..10


@dataclass(frozen=True)
class SkillRule:
    """From ``after_seconds`` of waiting in the queue on, the call needs agents matching ``condition``."""

    after_seconds: int
    condition: str  # contracts.skills language, e.g. "swahili >= 7 & billing >= 5"


@dataclass(frozen=True)
class QueueConfig:
    uid: str
    name: str
    ring_timeout: int = 20  # seconds an agent's phone rings before the next agent
    max_wait: int = 300  # seconds a caller waits before overflow
    retry_delay: int = 5  # seconds before agents who rejected this call are tried again
    moh_class: str = "default"
    wrapup_seconds: int = 0  # after-call work before the next offer; 0 = none
    members: tuple[str, ...] = ()  # agent uids, in member order (priority, then number)
    strategy: str = Strategy.LONGEST_IDLE
    max_callers: int = 0  # callers waiting at most; 0 = no limit
    priority: int = 0  # a free agent in several queues takes the highest-priority queue's caller first
    overflow_queue: str | None = None  # where a caller goes at max wait or max callers; None = hang up
    skill_rules: tuple[SkillRule, ...] = ()  # sorted by after_seconds; none = every member qualifies


@dataclass(frozen=True)
class Config:
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    queues: dict[str, QueueConfig] = field(default_factory=dict)
    entries: dict[str, str] = field(default_factory=dict)  # number dialled -> queue uid
