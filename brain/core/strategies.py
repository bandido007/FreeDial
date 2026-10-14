"""How a queue chooses among the agents who may take a call (brain.core.routing decides who may).

Every function gets the candidates in member order (free, matching the skill rule,
not yet tried for this call) and returns whom to ring. Pure: memory a strategy
needs (who was rung last, how many calls each answered) is passed in.
"""

import hashlib
from collections.abc import Callable, Mapping, Sequence

from brain.core.agents import Agent
from brain.core.config import Strategy


def longest_idle(candidates: Sequence[Agent]) -> Agent:
    return min(candidates, key=lambda agent: (agent.idle_since, agent.number))


def choose(
    strategy: str,
    candidates: Sequence[Agent],
    *,
    members: Sequence[str],
    last_rung: str | None,
    answered: Mapping[str, int],
    new_id: Callable[[], str],
    scores: Mapping[str, float] | None = None,
) -> list[Agent]:
    """The agent(s) to ring now. ``candidates`` is never empty. ``scores``: for best_skilled."""
    if strategy == Strategy.BEST_SKILLED:
        best = max((scores or {}).get(agent.uid, 0.0) for agent in candidates)
        return [longest_idle([agent for agent in candidates if (scores or {}).get(agent.uid, 0.0) == best])]
    if strategy == Strategy.RING_ALL:
        return list(candidates)
    if strategy == Strategy.LINEAR:
        return [candidates[0]]
    if strategy == Strategy.ROUND_ROBIN:
        return [next_after(candidates, members, last_rung)]
    if strategy == Strategy.FEWEST_CALLS:
        return [
            min(candidates, key=lambda agent: (answered.get(agent.uid, 0), agent.idle_since, agent.number))
        ]
    if strategy == Strategy.RANDOM:
        return [candidates[draw(new_id(), len(candidates))]]
    return [longest_idle(candidates)]  # LONGEST_IDLE, and anything unknown


def next_after(candidates: Sequence[Agent], members: Sequence[str], last_rung: str | None) -> Agent:
    """The first candidate after ``last_rung`` going round the member list (Asterisk's rrmemory)."""
    order = list(members)
    if last_rung not in order:
        return candidates[0]
    start = order.index(last_rung) + 1
    position = {uid: place for place, uid in enumerate(order[start:] + order[:start])}
    return min(candidates, key=lambda agent: position.get(agent.uid, len(position)))


def draw(seed: str, count: int) -> int:
    """A uniform pick in 0..count-1 from an id the core drew, so a replay repeats it exactly."""
    return int(hashlib.sha256(seed.encode()).hexdigest(), 16) % count
