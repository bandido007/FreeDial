"""The demo contact centre of docs/ROUTING_DESIGN.md section 3, as plain data.

A fictional public health-insurance helpline. ``manage.py seed_demo`` writes it to the
database; the brain's routing tests (T1-T12) build the core's configuration from it
directly, so both always describe the same centre.
"""

from typing import Any

from brain.core.config import AgentConfig, Config, EntryConfig, QueueConfig, Requirement, Step

LANGUAGE = "$language"

# skill -> kind
SKILLS = {
    "swahili": "language",
    "english": "language",
    "membership": "topic",
    "claims": "topic",
    "provider_support": "topic",
    "complaints": "topic",
    "tier2": "tier",
}

TEAMS = {"Front Desk": "Asha", "Claims & Providers": "Daudi"}  # team -> supervisor

# (name, number, team, skill levels, queues in which they are members)
AGENTS: list[tuple[str, str, str, dict[str, int], tuple[str, ...]]] = [
    (
        "Juma",
        "7101",
        "Front Desk",
        {"swahili": 9, "english": 5, "membership": 8, "claims": 4},
        ("Membership", "Claims", "General"),
    ),
    (
        "Neema",
        "7102",
        "Front Desk",
        {"swahili": 9, "english": 8, "membership": 6, "claims": 7},
        ("Membership", "Claims", "General"),
    ),
    (
        "Baraka",
        "7103",
        "Front Desk",
        {"swahili": 7, "english": 9, "membership": 5},
        ("Membership", "General"),
    ),
    ("Rehema", "7104", "Front Desk", {"swahili": 9, "membership": 3}, ("Membership", "General")),
    (
        "Halima",
        "7105",
        "Claims & Providers",
        {"swahili": 8, "english": 8, "claims": 9, "provider_support": 7},
        ("Claims", "Providers"),
    ),
    (
        "Peter",
        "7106",
        "Claims & Providers",
        {"swahili": 5, "english": 9, "claims": 7, "provider_support": 8},
        ("Claims", "Providers"),
    ),
    (
        "Zawadi",
        "7107",
        "Claims & Providers",
        {"swahili": 9, "english": 4, "claims": 6, "complaints": 8, "tier2": 7},
        ("Claims", "Escalations"),
    ),
    (
        "Mussa",
        "7108",
        "Claims & Providers",
        {"swahili": 8, "english": 7, "provider_support": 6, "complaints": 9, "tier2": 9},
        ("Escalations", "Providers"),
    ),
]

# rule set -> steps: (until_seconds or None = forever, {skill or $language: min level})
RULE_SETS: dict[str, list[tuple[int | None, dict[str, int]]]] = {
    "R_MEMBERSHIP": [
        (30, {LANGUAGE: 6, "membership": 6}),
        (90, {LANGUAGE: 5, "membership": 3}),
        (None, {LANGUAGE: 4, "membership": 1}),
    ],
    "R_CLAIMS": [
        (30, {LANGUAGE: 6, "claims": 7}),
        (90, {LANGUAGE: 5, "claims": 4}),
        (None, {LANGUAGE: 4, "claims": 1}),
    ],
    "R_PROVIDERS": [(20, {LANGUAGE: 5, "provider_support": 7}), (None, {LANGUAGE: 4, "provider_support": 4})],
    "R_ESCALATIONS": [(None, {LANGUAGE: 6, "tier2": 7, "complaints": 6})],
    "R_GENERAL": [(None, {LANGUAGE: 4})],
}

# name: rule set, strategy, priority, ring timeout, max wait, overflow (action, queue), service level (s, %)
QUEUES: dict[str, dict[str, Any]] = {
    "Membership": dict(
        rule_set="R_MEMBERSHIP",
        strategy="longest_idle",
        priority=5,
        ring_timeout=15,
        max_wait=300,
        overflow=("queue", "General"),
        service_level=(30, 80),
    ),
    "Claims": dict(
        rule_set="R_CLAIMS",
        strategy="longest_idle",
        priority=5,
        ring_timeout=15,
        max_wait=300,
        overflow=("voicemail", None),
        service_level=(30, 80),
    ),
    "Providers": dict(
        rule_set="R_PROVIDERS",
        strategy="longest_idle",
        priority=10,
        ring_timeout=15,
        max_wait=180,
        overflow=("queue", "Claims"),
        service_level=(20, 90),
    ),
    "Escalations": dict(
        rule_set="R_ESCALATIONS",
        strategy="best_skilled",
        priority=8,
        ring_timeout=20,
        max_wait=120,
        overflow=("voicemail", None),
        service_level=(60, 80),
    ),
    "General": dict(
        rule_set="R_GENERAL",
        strategy="longest_idle",
        priority=3,
        ring_timeout=15,
        max_wait=600,
        overflow=("voicemail", None),
        service_level=(60, 70),
    ),
}

# (number, queue, language, priority or None = the queue's, fallback queue). On 800 and 802 the AI
# agent "Jambo AI" answers first (seed_demo, apps/ai/demo.py); the routing tests start at the queue.
ENTRIES = [
    ("800", "General", "swahili", None, "General"),
    ("801", "Providers", "swahili", 10, "Claims"),
    ("802", "General", "english", None, "General"),
]


def agent_uid(name: str) -> str:
    return f"agent-{name.lower()}"


def queue_uid(name: str) -> str:
    return name.lower()


def steps(rule_set: str) -> tuple[Step, ...]:
    return tuple(
        Step(
            until, tuple(Requirement(skill, level) for skill, level in sorted(needs.items()))
        )  # as the loader orders
        for until, needs in RULE_SETS[rule_set]
    )


def core_config() -> Config:
    """The demo centre as the brain's core sees it (uids are readable names here)."""
    agents = {
        agent_uid(name): AgentConfig(agent_uid(name), number, name, dict(levels))
        for name, number, _t, levels, _q in AGENTS
    }
    queues = {}
    for name, settings in QUEUES.items():
        action, target = settings["overflow"]
        queues[queue_uid(name)] = QueueConfig(
            uid=queue_uid(name),
            name=name,
            strategy=settings["strategy"],
            priority=settings["priority"],
            ring_timeout=settings["ring_timeout"],
            max_wait=settings["max_wait"],
            wrapup_seconds=0,
            overflow_action=action,
            overflow_queue=queue_uid(target) if target else None,
            steps=steps(settings["rule_set"]),
            members=tuple(agent_uid(agent) for agent, _n, _t, _l, member_of in AGENTS if name in member_of),
        )
    entries = {
        number: EntryConfig(number, queue_uid(queue), language, priority, queue_uid(fallback))
        for number, queue, language, priority, fallback in ENTRIES
    }
    return Config(agents=agents, queues=queues, entries=entries, skills=dict(SKILLS))
