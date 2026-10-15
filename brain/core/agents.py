"""An agent's live state: availability (what the agent chose) and activity (what is happening).

Availability changes only on the agent's request (commands). Activity changes only
because of calls. Only the brain writes either (rule 2).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Availability(StrEnum):
    LOGGED_OUT = "logged_out"
    READY = "ready"
    NOT_READY = "not_ready"


class Activity(StrEnum):
    IDLE = "idle"
    OFFERED = "offered"  # the phone is ringing with a call
    TALKING = "talking"
    ON_HOLD = "on_hold"
    WRAPUP = "wrapup"
    NOT_RESPONDING = "not_responding"  # missed an offer; offered nothing until Ready again


@dataclass
class Agent:
    uid: str
    number: str
    name: str
    availability: Availability = Availability.LOGGED_OUT
    activity: Activity = Activity.IDLE
    reason: str = ""  # Not Ready reason, or why the agent is not responding
    endpoint: str | None = None  # where the agent takes calls, e.g. PJSIP/1001
    call: str | None = None
    wrapup_call: str | None = None  # the call the agent is wrapping up
    idle_since: float = 0.0  # longest idle is offered first
    wrapup_started_at: float = 0.0
    last_wrap: tuple[str, str, float] | None = None  # (call, code, when): correctable for 2 minutes
    removed: bool = False  # gone from the configuration; dropped once its call ends

    @property
    def can_take_a_call(self) -> bool:
        return (
            self.availability is Availability.READY
            and self.activity is Activity.IDLE
            and self.endpoint is not None
            and not self.removed
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "agent": self.uid,
            "number": self.number,
            "availability": str(self.availability),
            "activity": str(self.activity),
            "reason": self.reason,
            "call": self.call,
            "wrapup_call": self.wrapup_call,
            "endpoint": self.endpoint,
            "idle_since": self.idle_since,
        }

    def restore(self, saved: dict[str, Any]) -> None:
        """Take back a state saved before a restart (activity is fixed by reconcile)."""
        self.availability = Availability(saved.get("availability", Availability.LOGGED_OUT))
        self.activity = Activity(saved.get("activity", Activity.IDLE))
        self.reason = saved.get("reason") or ""
        self.endpoint = saved.get("endpoint")
        self.call = saved.get("call")
        self.wrapup_call = saved.get("wrapup_call")
        self.idle_since = float(saved.get("idle_since") or 0.0)
