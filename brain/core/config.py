"""The configuration the core decides with: skills, agents, queues and entry points.

Plain frozen data. ``brain.adapters.config`` builds it from the database at start
and on every ``config.changed`` command; the core never reads the database.
Routing follows docs/ROUTING_DESIGN.md.
"""

from dataclasses import dataclass, field
from enum import StrEnum

LANGUAGE = "$language"  # in a requirement: the call's language skill (e.g. swahili)


class Strategy(StrEnum):
    """Which matching agent(s) a queue rings."""

    LONGEST_IDLE = "longest_idle"  # the agent idle the longest
    BEST_SKILLED = "best_skilled"  # highest average level of the step's required skills, ties to longest idle
    RING_ALL = "ring_all"  # every matching agent at once; the first to answer takes it
    ROUND_ROBIN = "round_robin"  # the next member after the one rung last, in member order
    LINEAR = "linear"  # always the first matching member in member order
    FEWEST_CALLS = "fewest_calls"  # the agent who answered the fewest calls of this queue
    RANDOM = "random"


class OverflowAction(StrEnum):
    QUEUE = "queue"  # on to the overflow queue
    VOICEMAIL = "voicemail"  # the caller leaves a message; a callback task is created
    HANGUP = "hangup"


@dataclass(frozen=True)
class AgentConfig:
    uid: str
    number: str
    name: str
    skills: dict[str, int] = field(default_factory=dict)  # skill name -> level 1..10


@dataclass(frozen=True)
class Requirement:
    skill: str  # a skill name, or LANGUAGE
    min_level: int


@dataclass(frozen=True)
class Step:
    """Until ``until_seconds`` of waiting (None: forever), an agent needs every requirement."""

    until_seconds: int | None
    requirements: tuple[Requirement, ...]


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
    priority: int = 0  # a call's priority unless its entry point sets one
    overflow_action: str = OverflowAction.HANGUP
    overflow_queue: str | None = None  # for OverflowAction.QUEUE
    steps: tuple[Step, ...] = ()  # the skill rule set; none = every member qualifies


@dataclass(frozen=True)
class EntryConfig:
    """A number callers dial, and the routing ticket it starts the call with."""

    number: str
    queue: str  # queue uid
    language: str | None = None  # language skill, e.g. swahili
    priority: int | None = None  # None: the queue's priority
    fallback_queue: str | None = None  # where a rejected AI decision sends the call


@dataclass(frozen=True)
class Config:
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    queues: dict[str, QueueConfig] = field(default_factory=dict)
    entries: dict[str, EntryConfig] = field(default_factory=dict)  # number dialled -> entry
    skills: dict[str, str] = field(default_factory=dict)  # skill name -> kind (language, topic, tier)
    dial_template: str = (
        "PJSIP/{number}"  # how a transfer to a number is dialled (a trunk: PJSIP/{number}@trunk)
    )
