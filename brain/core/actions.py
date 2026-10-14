"""What the core asks the outside world to do.

The core never performs I/O: every decision returns a list of these, and the
service carries them out. ARI actions carry ``call`` so the service runs them on
that call's own queue, in order. ``cleanup`` actions always run, even after an
earlier action of the same decision failed (rule 8).
"""

from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass(frozen=True)
class Action:
    cleanup: ClassVar[bool] = False


@dataclass(frozen=True)
class AriAction(Action):
    call: str


@dataclass(frozen=True)
class Answer(AriAction):
    channel: str


@dataclass(frozen=True)
class CreateBridge(AriAction):
    bridge: str
    name: str


@dataclass(frozen=True)
class AddToBridge(AriAction):
    bridge: str
    channel: str


@dataclass(frozen=True)
class RemoveFromBridge(AriAction):
    bridge: str
    channel: str


@dataclass(frozen=True)
class StartMoh(AriAction):
    bridge: str
    moh_class: str


@dataclass(frozen=True)
class StopMoh(AriAction):
    bridge: str


@dataclass(frozen=True)
class Originate(AriAction):
    """Ring an agent into the app. ``originator`` makes the leg share the caller's linkedid (rule 4)."""

    channel: str
    endpoint: str
    app_args: str
    caller_id: str
    originator: str
    timeout: int


@dataclass(frozen=True)
class Play(AriAction):
    """Play a sound to a channel; Asterisk answers with PlaybackFinished for ``playback_id``."""

    channel: str
    media: str  # e.g. sound:vm-intro
    playback_id: str


@dataclass(frozen=True)
class Record(AriAction):
    """Record a channel to a stored recording ``name`` (wav); Asterisk answers with RecordingFinished."""

    channel: str
    name: str
    max_seconds: int = 120
    max_silence_seconds: int = 10
    beep: bool = True


@dataclass(frozen=True)
class Hangup(AriAction):
    channel: str
    reason: str = "normal"
    cleanup: ClassVar[bool] = True


@dataclass(frozen=True)
class DeleteBridge(AriAction):
    bridge: str
    cleanup: ClassVar[bool] = True


@dataclass(frozen=True)
class Close(AriAction):
    """The call is over: its queue may stop once everything before this ran."""

    cleanup: ClassVar[bool] = True


@dataclass(frozen=True)
class Emit(Action):
    """Publish an event (contracts.messages) on the events stream."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    command_id: str | None = None


@dataclass(frozen=True)
class SaveAgent(Action):
    """Store the agent's live state (Redis), so a restarted brain and the web can read it."""

    agent: str
    state: dict[str, Any] | None  # None: forget the agent


@dataclass(frozen=True)
class SetTimer(Action):
    key: str
    seconds: float


@dataclass(frozen=True)
class CancelTimer(Action):
    key: str
