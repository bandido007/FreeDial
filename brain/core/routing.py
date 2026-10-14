"""The matcher: which agents may take a call, now (docs/ROUTING_DESIGN.md "Rule semantics").

One pure function, :func:`match`, answers both directions: a call looking for an agent
(it enters a queue, or reaches a new step) and an agent looking for a call (it becomes
Ready: the brain walks the waiting calls, highest priority then longest wait, and asks
the same question), so the two can never disagree.

- The active step is the first one with ``waited < until_seconds``; a ``None`` step lasts
  forever. At exactly 30 s a step "until 30" has ended.
- An agent matches when they have **every** required skill at ``>= min_level``. A missing
  skill never matches. ``$language`` is the call's language skill.
- Extra requirements on the ticket (from the AI or a transfer) apply on top of every step.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from brain.core.config import LANGUAGE, QueueConfig, Requirement, Step


@dataclass(frozen=True)
class Ticket:
    """Where a call is routed and what it needs. Later sources win: entry point, then AI, then transfer."""

    queue: str
    language: str | None = None
    priority: int | None = None  # None: the queue's priority
    extra: tuple[Requirement, ...] = ()
    source: str = "entry_point"


@dataclass(frozen=True)
class Match:
    agents: tuple[str, ...]  # matching agent uids, in the order given (member order)
    step: Step | None  # the step in force (None: the queue has no rule set)
    next_change: int | None  # seconds of waiting at which the next step starts; None: never
    needs: tuple[tuple[str, int], ...] = field(default=())  # the requirements actually checked


def active_step(steps: tuple[Step, ...], waited: float) -> tuple[Step | None, int | None]:
    for step in steps:
        if step.until_seconds is None or waited < step.until_seconds:
            return step, step.until_seconds
    return (steps[-1], None) if steps else (None, None)  # past a rule set without a forever step


def needs(step: Step | None, ticket: Ticket) -> tuple[tuple[str, int], ...]:
    """The step's and the ticket's requirements as (skill, min level), with $language resolved.

    A call without a language skips $language (there is nothing to require).
    """
    out: dict[str, int] = {}
    for requirement in (*(step.requirements if step else ()), *ticket.extra):
        skill = ticket.language if requirement.skill == LANGUAGE else requirement.skill
        if skill:
            out[skill] = max(out.get(skill, 0), requirement.min_level)
    return tuple(out.items())


def qualifies(levels: Mapping[str, int], required: tuple[tuple[str, int], ...]) -> bool:
    return all(skill in levels and levels[skill] >= minimum for skill, minimum in required)


def skill_score(levels: Mapping[str, int], step: Step | None, ticket: Ticket) -> float:
    """best_skilled: the average level of the step's required skills (0 if it requires none)."""
    required = needs(step, ticket)
    return sum(levels.get(skill, 0) for skill, _ in required) / len(required) if required else 0.0


def match(
    queue: QueueConfig,
    ticket: Ticket,
    waited: float,
    skills: Mapping[str, Mapping[str, int]],
    free: Iterable[str],
) -> Match:
    """The free agents (uids, in the order given) who match the step in force after ``waited`` s."""
    step, next_change = active_step(queue.steps, waited)
    required = needs(step, ticket)
    agents = tuple(uid for uid in free if qualifies(skills.get(uid, {}), required))
    return Match(agents=agents, step=step, next_change=next_change, needs=required)
