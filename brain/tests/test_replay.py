"""Replay what the real lab recorded: the core must decide exactly what it decided then.

``brain/sim/recordings/lab.jsonl`` is written by ``run_brain --record`` while
lab/calltests.py drives the real Asterisk (call tests 1-8, two brain restarts).
Each line is one decision: its input as Asterisk or the bus gave it, the ids the
core drew and the actions it chose. Re-record after a deliberate change in behaviour.
"""

import json
from pathlib import Path

import pytest

from brain.core.brain import Brain
from brain.core.config import AgentConfig, AiAgentConfig, Config, EntryConfig, QueueConfig, Requirement, Step
from brain.service.runtime import deserialize, serialize

RECORDINGS = Path(__file__).resolve().parent.parent / "sim" / "recordings"


def config_from(data: dict) -> Config:
    """A recorded configuration, in whichever shape the recording's brain wrote it."""
    known = set(QueueConfig.__dataclass_fields__)

    def queue(fields: dict) -> QueueConfig:
        fields = {key: value for key, value in fields.items() if key in known}
        fields["members"] = tuple(fields.get("members", ()))
        fields["steps"] = tuple(
            Step(step["until_seconds"], tuple(Requirement(**need) for need in step["requirements"]))
            for step in fields.get("steps", ())
        )
        return QueueConfig(**fields)

    def entry(number: str, value) -> EntryConfig:
        return (
            EntryConfig(number, value) if isinstance(value, str) else EntryConfig(**value)
        )  # before M4.1: a queue uid

    return Config(
        agents={uid: AgentConfig(**agent) for uid, agent in data["agents"].items()},
        queues={uid: queue(fields) for uid, fields in data["queues"].items()},
        entries={number: entry(number, value) for number, value in data["entries"].items()},
        skills=dict(data.get("skills", {})),
        ai_agents={uid: AiAgentConfig(**ai) for uid, ai in data.get("ai_agents", {}).items()},  # since M8
        dial_template=data.get("dial_template", Config.dial_template),
    )


def decide(core: Brain, kind: str, given: dict, now: float):
    match kind:
        case "ari":
            return core.on_ari(given["event"], now)
        case "reconcile":
            return core.reconcile(given["channels"], given["bridges"], given["connected_at"], now)
        case "command":
            return core.on_command(given["type"], given["data"], given["command_id"], now)
        case "timer":
            return core.on_timer(given["key"], now)
        case "failed":
            return core.on_failed(deserialize(given["action"]), given["error"], now)
        case "config":
            return core.load_config(config_from(given["config"]), now)
    raise AssertionError(f"unknown decision kind {kind}")


@pytest.mark.parametrize("recording", sorted(RECORDINGS.glob("*.jsonl")), ids=lambda path: path.name)
def test_the_core_decides_as_it_did_on_the_lab(recording):
    core = None
    decisions = 0
    for number, text in enumerate(recording.read_text().splitlines(), start=1):
        line = json.loads(text)
        if line["kind"] == "start":  # a brain process started: fresh memory, saved agent states
            core = Brain(config_from(line["config"]), app=line["app"], new_id=lambda: "unused")
            core.restore_agents(line["agents"])
            continue
        assert core is not None, "a recording starts with a start line"
        ids = iter(line["ids"])
        core.new_id = lambda ids=ids: next(ids)
        actions = decide(core, line["kind"], line["input"], line["now"])
        replayed = json.loads(json.dumps([serialize(action) for action in actions], default=str))
        assert replayed == line["actions"], (
            f"{recording.name} line {number}: {line['kind']} decided differently"
        )
        decisions += 1
    assert decisions > 100
