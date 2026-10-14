"""The envelope every command and event travels in, version 1.

A **command** asks the brain to do something and carries a ``command_id``; an
**event** says something happened and carries an ``event_id``. Delivery is
at-least-once, so receivers use these ids to ignore a message they have already
handled.

Within a version, changes are additive only (a new optional field, a new
``type``). Anything that would break an existing reader is a new version.
The command and event types themselves are declared alongside this module as
the milestones add them.
"""

import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from typing import Any

VERSION = 1


class ContractError(ValueError):
    """A message that does not follow the contract."""


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Command:
    type: str
    data: dict[str, Any]
    command_id: str = field(default_factory=_new_id)
    sent_at: str = field(default_factory=_now)
    version: int = VERSION


@dataclass(frozen=True)
class Event:
    type: str
    data: dict[str, Any]
    event_id: str = field(default_factory=_new_id)
    at: str = field(default_factory=_now)
    version: int = VERSION
    command_id: str | None = None  # the command that caused it, when there was one


_KINDS: dict[str, tuple[type[Command] | type[Event], str]] = {
    "command": (Command, "command_id"),
    "event": (Event, "event_id"),
}


def encode(message: Command | Event) -> str:
    kind = "command" if isinstance(message, Command) else "event"
    return json.dumps({"kind": kind, **asdict(message)}, separators=(",", ":"))


def decode(text: str) -> Command | Event:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(f"not JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ContractError("a message is a JSON object")

    kind = raw.pop("kind", None)
    if kind not in _KINDS:
        raise ContractError(f"unknown kind {kind!r}")
    if raw.get("version") != VERSION:
        raise ContractError(f"unsupported version {raw.get('version')!r}, this reader knows {VERSION}")

    cls, id_field = _KINDS[kind]
    if not raw.get(id_field):
        raise ContractError(f"a {kind} must carry a non-empty {id_field}")
    # A newer writer of the same version may add optional fields; this reader skips them.
    known = {f.name for f in fields(cls)}
    try:
        return cls(**{name: value for name, value in raw.items() if name in known})
    except TypeError as exc:
        raise ContractError(f"bad {kind}: {exc}") from exc
