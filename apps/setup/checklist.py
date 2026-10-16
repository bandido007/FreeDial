"""The setup checklist (admin -> Setup guide): is the contact centre set up, and does it work?

Every check is read live from the configuration, the call records and Redis, so it is never out of
date. Only the hands-on tests (``HANDS_ON``: what no record can prove, such as both sides hearing each
other) are confirmed by a person, through ``apps.setup.services``.

A check is DONE, needs ATTENTION (something is wrong: ``problems`` names each thing, with a link to
fix it), is TODO (not done yet) or NOT_USED (this centre does not use that feature). ``notes`` are
worth knowing but do not change the status.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import redis
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.agents.models import Agent, AgentSession, NotReadyReason, Skill, WrapupCode
from apps.ai.models import AiAgent, AiRoute
from apps.calls.models import Call, Event, InteractionPart
from apps.core.crypto import decrypt_secret
from apps.core.enums import AccountStatusChoices, AuthProviderChoices
from apps.core.hooks import resolve_permissions
from apps.queues.models import LANGUAGE, EntryPoint, Queue, SkillRuleSet
from apps.queues.services import rule_steps
from apps.rbac.models import UserRole
from apps.teams.models import Team
from apps.telephony.models import SipAccount
from apps.telephony.services import BROWSER_PREFIX

DONE, ATTENTION, TODO, NOT_USED = "done", "attention", "todo", "not_used"
LABELS = {DONE: "Done", ATTENTION: "Needs attention", TODO: "Not done yet", NOT_USED: "Not used"}

# Asterisk's [lab] dialplan hands the pattern _8XX (800 to 899) to AlgoDial; any other number never
# reaches the brain (CLAUDE.md, "Dialplan entry").
ENTRY_NUMBER = re.compile(r"^8\d\d$")
RECENT = timedelta(days=30)  # a test older than this proves little about today's setup
CHECK_IN_LIMIT_MS = 30_000  # a running service reads its stream at least every 5 s
SERVICES = {  # name: (stream, consumer group, how to start it)
    "brain": (settings.BUS_COMMANDS_STREAM, "brain", "manage.py run_brain"),
    "writer": (settings.BUS_EVENTS_STREAM, "writer", "manage.py run_writer"),
    "ai": (settings.BUS_EVENTS_STREAM, "ai", "manage.py run_ai"),
}
SUPERVISION = {"can_monitor_calls", "can_whisper_calls", "can_barge_calls", "can_force_agent_state"}
ANSWERED = ("answered", "ai_handled")

# The hands-on tests: (title, how to do it). A person confirms each one on the page.
HANDS_ON = {
    "confirm.audio": (
        "Both sides heard each other clearly",
        "Call 800 from a phone, answer on the agent page and talk for a minute. Both voices clear, "
        "no delay or echo.",
    ),
    "confirm.phones": (
        "Every desk phone is registered",
        "Each Zoiper (or other SIP phone) shows Registered, and calling it from another phone rings.",
    ),
    "confirm.whisper": (
        "Whisper is heard by the agent only",
        "During a test call a supervisor chooses Whisper: the agent hears them, the caller does not.",
    ),
    "confirm.fallback": (
        "Calls still get through when the brain is down",
        "Stop only the brain and call 800: Asterisk's backup queue answers and phones 1001 and 1002 ring. "
        "Start the brain again afterwards.",
    ),
}


@dataclass(frozen=True)
class Line:
    text: str
    url: str = ""


@dataclass
class Check:
    key: str
    title: str
    status: str
    summary: str
    problems: list[Line] = field(default_factory=list)
    notes: list[Line] = field(default_factory=list)
    fix: Line | None = None  # where to go to do it
    manual: str = ""  # the manual's section (anchor id)
    hands_on: bool = False
    how: str = ""  # hands-on: what to try

    @property
    def label(self) -> str:
        return LABELS[self.status]

    @property
    def anchor(self) -> str:
        return f"check-{self.key}"


@dataclass
class Section:
    title: str
    intro: str
    checks: list[Check]


# ── helpers ──────────────────────────────────────────────────────────────────
def change_url(obj) -> str:
    meta = obj._meta
    return reverse(f"admin:{meta.app_label}_{meta.model_name}_change", args=[obj.pk])


def list_url(model) -> str:
    meta = model._meta
    return reverse(f"admin:{meta.app_label}_{meta.model_name}_changelist")


def add_url(model) -> str:
    meta = model._meta
    return reverse(f"admin:{meta.app_label}_{meta.model_name}_add")


def when(moment: datetime) -> str:
    return f"{timezone.localtime(moment):%d %b %Y, %H:%M}"


def holds(user, codes: set[str]) -> bool:
    return user.is_superuser or bool(codes & resolve_permissions(user))


def item(key, title, problems=(), notes=(), *, done, todo=None, summary_problem="", **extra) -> Check:
    """DONE with ``done`` as summary; ATTENTION when there are problems; TODO when ``todo`` is given."""
    problems, notes = list(problems), list(notes)
    if todo:
        status, summary = TODO, todo
    elif problems:
        count = len(problems)
        status, summary = ATTENTION, summary_problem or f"{count} thing{'s' * (count != 1)} to fix"
    else:
        status, summary = DONE, done
    return Check(key, title, status, summary, problems, notes, **extra)


def read_consumers() -> dict[str, int | None] | None:
    """Each service's time since it last read its stream (ms), None if it never joined; None if Redis
    cannot be reached."""
    from apps.calls.bus import client

    found: dict[str, int | None] = {}
    try:
        connection = client()
        for name, (stream, group, _) in SERVICES.items():
            try:
                consumers: list[dict] = connection.xinfo_consumers(stream, group)  # type: ignore[assignment]
            except redis.ResponseError:  # no such stream or group yet: never started
                found[name] = None
                continue
            found[name] = min((int(c["idle"]) for c in consumers), default=None)
    except redis.RedisError:
        return None
    return found


# ── the sections ─────────────────────────────────────────────────────────────
class Centre:
    """What the checks look at, read once."""

    def __init__(self, now: datetime):
        self.now, self.since = now, now - RECENT
        self.agents = list(Agent.objects.filter(active=True).select_related("user"))
        self.queues = list(Queue.objects.filter(active=True).select_related("skill_rule_set"))
        self.entries = list(
            EntryPoint.objects.filter(active=True).select_related(
                "queue", "fallback_queue", "language", "ai_agent"
            )
        )
        self.ai_entries = [entry for entry in self.entries if entry.ai_agent_id]
        self.teams = list(Team.objects.prefetch_related("supervisors", "agents"))
        self.members = {
            queue.pk: [agent for agent in queue.agents.filter(active=True)] for queue in self.queues
        }


def services_section(centre: Centre, consumers) -> Section:
    def service(name, title, *, used=True) -> Check:
        key = f"services.{name}"
        _, _, start = SERVICES[name]
        if not used:
            return Check(key, title, NOT_USED, "No number has an AI agent.", manual="manual-running")
        if consumers is None:
            return Check(
                key,
                title,
                ATTENTION,
                "Cannot reach Redis, so this cannot be checked. Start it with make up.",
                manual="manual-running",
            )
        idle = consumers.get(name)
        if idle is None:
            summary = f"Not running: it has never started. Start make dev (or {start})."
        elif idle > CHECK_IN_LIMIT_MS:
            summary = f"Not running: last seen {idle // 60000} min ago. Start make dev (or {start})."
        else:
            return Check(key, title, DONE, "Running.", manual="manual-running")
        return Check(key, title, ATTENTION, summary, manual="manual-running")

    return Section(
        "Running services",
        "Nothing else works without these. They run under make dev.",
        [
            service("brain", "The brain (makes every call decision)"),
            service("writer", "The writer (keeps call records and reports)"),
            service("ai", "The AI worker (answers AI numbers)", used=bool(centre.ai_entries)),
        ],
    )


def people_section(centre: Centre) -> Section:
    with_user = [agent for agent in centre.agents if agent.user_id]
    no_agents = "No agents yet." if not centre.agents else None
    sign_in, notes = [], []
    for agent in centre.agents:
        user = agent.user
        if user is None:
            notes.append(
                Line(
                    f"{agent} has no user, so nobody can sign in as it "
                    "(fine for the call tests' scripted agents).",
                    change_url(agent),
                )
            )
            continue
        why = []
        if user.deleted or not user.is_active:
            why.append("the user is switched off (Active is not ticked)")
        if user.account_status != AccountStatusChoices.ACTIVE.value:
            why.append(f"the account is {user.account_status.lower()}")
        if user.auth_provider == AuthProviderChoices.DB.value and not user.is_verified:
            why.append('"Is verified" is not ticked')
        if user.must_change_password:
            why.append('"Must change password" is ticked, and the agent page cannot set a new one yet')
        if why:
            sign_in.append(Line(f"{agent} ({user.email}): {'; '.join(why)}.", change_url(user)))
    agent_role = [
        Line(f"{agent} ({agent.user.email}) does not have the Agent role.", add_url(UserRole))
        for agent in with_user
        if agent.user and not holds(agent.user, {"can_take_calls"})
    ]
    supervisors = {user.pk: user for team in centre.teams for user in team.supervisors.all()}
    supervisor_role = [
        Line(
            f"{user.get_full_name() or user.email} supervises a team but has no Supervisor role.",
            add_url(UserRole),
        )
        for user in supervisors.values()
        if not holds(user, SUPERVISION)
    ]
    return Section(
        "People and sign-in",
        "Everyone signs in with their email. A role decides what they may do.",
        [
            item(
                "people.sign_in",
                "Every agent's user can sign in",
                sign_in,
                notes,
                done=f"{len(with_user)} agent{'s' * (len(with_user) != 1)} can sign in.",
                todo=no_agents,
                fix=Line("Users", list_url(get_user_model())),
                manual="manual-people",
            ),
            item(
                "people.agent_role",
                "Agents hold the Agent role",
                agent_role,
                done="Every agent's user may take calls.",
                todo=None if with_user else "No agent has a user yet.",
                fix=Line("User roles", list_url(UserRole)),
                manual="manual-roles",
            ),
            item(
                "people.supervisor_role",
                "Supervisors hold the Supervisor role",
                supervisor_role,
                done=f"{len(supervisors)} supervisor{'s' * (len(supervisors) != 1)} may watch their teams.",
                todo=None if supervisors else "No team has a supervisor yet.",
                fix=Line("User roles", list_url(UserRole)),
                manual="manual-roles",
            ),
        ],
    )


def agents_section(centre: Centre) -> Section:
    in_a_queue = {agent.pk for members in centre.members.values() for agent in members}
    loose = [
        Line(f"{agent} is in no queue, so no call is ever offered to them.", change_url(agent))
        for agent in centre.agents
        if agent.pk not in in_a_queue
    ]
    languages = list(Skill.objects.filter(kind="language", active=True).values_list("name", flat=True))
    codes = WrapupCode.objects.filter(active=True).count()
    reasons = NotReadyReason.objects.filter(active=True).count()
    checks = [
        item(
            "agents.in_queue",
            "Every agent is in at least one queue",
            loose,
            done="Every agent can be offered calls.",
            todo=None if centre.agents else "No agents yet.",
            fix=Line("Queues", list_url(Queue)),
            manual="manual-queues",
        ),
        Check(
            "agents.languages",
            "Languages are skills",
            DONE if languages else NOT_USED,
            f"Languages: {', '.join(languages)}."
            if languages
            else "No languages: any member takes any caller.",
            fix=Line("Skills", list_url(Skill)),
            manual="manual-skills",
        ),
        item(
            "agents.wrapup_codes",
            "Wrap-up codes exist",
            done=f"{codes} wrap-up code{'s' * (codes != 1)}.",
            todo=None if codes else "None yet: agents need one to finish a call.",
            fix=Line("Wrap-up codes", list_url(WrapupCode)),
            manual="manual-picklists",
        ),
        item(
            "agents.not_ready_reasons",
            "Not Ready reasons exist",
            done=f"{reasons} reason{'s' * (reasons != 1)}.",
            todo=None if reasons else "None yet: agents need one to go Not Ready.",
            fix=Line("Not Ready reasons", list_url(NotReadyReason)),
            manual="manual-picklists",
        ),
    ]
    return Section("Agents", "Who takes calls, what they know, and what they pick on their page.", checks)


def _languages_into(centre: Centre, queue: Queue) -> set[str]:
    """The caller languages that can reach ``queue``: from its numbers, and from AI numbers whose AI may
    send callers there."""
    ai_queues = {(route.ai_agent_id, route.queue_id) for route in AiRoute.objects.filter(queue=queue)}
    found = set()
    for entry in centre.entries:
        reaches = queue.pk in (entry.queue_id, entry.fallback_queue_id) or (
            (entry.ai_agent_id, queue.pk) in ai_queues
        )
        if reaches and entry.language:
            found.add(entry.language.name)
    return found


def _levels(agent: Agent) -> dict[str, int]:
    return {row.skill.name: row.level for row in agent.skills.select_related("skill") if row.skill.active}


def queues_section(centre: Centre) -> Section:
    empty = [
        Line(f"{queue.name} has no active agents: its callers wait, then overflow.", change_url(queue))
        for queue in centre.queues
        if not centre.members[queue.pk]
    ]
    reached = {entry.queue_id for entry in centre.entries} | {
        entry.fallback_queue_id for entry in centre.entries
    }
    reached |= {queue.overflow_queue_id for queue in centre.queues}
    reached |= set(AiRoute.objects.values_list("queue_id", flat=True))
    unreached = [
        Line(
            f"{queue.name}: no number, AI route or overflow leads here (only transfers can).",
            change_url(queue),
        )
        for queue in centre.queues
        if queue.pk not in reached
    ]
    skills, ruled = [], [queue for queue in centre.queues if queue.skill_rule_set_id]
    for queue in ruled:
        rule_set = queue.skill_rule_set
        assert rule_set is not None  # ruled: only queues with a rule set
        _, needs = rule_steps(rule_set)[-1]
        members = [_levels(agent) for agent in centre.members[queue.pk]]
        languages: list[str | None] = [*sorted(_languages_into(centre, queue))] or [None]
        for language in languages:
            # $language is the caller's language; with no language known, only the other skills count.
            wanted: dict[str, int] = {}
            for skill, level in needs.items():
                if skill != LANGUAGE:
                    wanted[skill] = level
                elif language is not None:
                    wanted[language] = level
            if not any(all(levels.get(s, 0) >= lvl for s, lvl in wanted.items()) for levels in members):
                asks = ", ".join(f"{skill} {level}" for skill, level in sorted(wanted.items()))
                skills.append(
                    Line(
                        f"{queue.name}: nobody meets the last step of {rule_set.name} ({asks}), "
                        "so these callers wait, then overflow.",
                        change_url(queue),
                    )
                )
    return Section(
        "Queues",
        "A queue is a line of business: who may take its calls, in what order, and what happens when "
        "nobody answers.",
        [
            item(
                "queues.members",
                "Every queue has agents",
                empty,
                unreached,
                done=f"{len(centre.queues)} queue{'s' * (len(centre.queues) != 1)}, each with agents.",
                todo=None if centre.queues else "No queues yet.",
                fix=Line("Queues", list_url(Queue)),
                manual="manual-queues",
            ),
            Check(
                "queues.skills",
                "Someone in each queue meets its skill rules",
                NOT_USED,
                "No queue uses a skill rule set: every member may take every call.",
                manual="manual-rules",
            )
            if not ruled
            else item(
                "queues.skills",
                "Someone in each queue meets its skill rules",
                skills,
                done="Every queue with skill rules has an agent who meets them.",
                fix=Line("Skill rule sets", list_url(SkillRuleSet)),
                manual="manual-rules",
            ),
        ],
    )


def entries_section(centre: Centre) -> Section:
    wrong = []
    for entry in centre.entries:
        if not ENTRY_NUMBER.match(entry.number):
            wrong.append(
                Line(
                    f"{entry.number}: Asterisk only sends 800 to 899 to AlgoDial, so this number never "
                    "reaches it.",
                    change_url(entry),
                )
            )
        if not entry.queue.active or entry.queue.deleted:
            wrong.append(
                Line(f"{entry.number} goes to {entry.queue.name}, which is switched off.", change_url(entry))
            )
    ai_problems = []
    if centre.ai_entries and not settings.OPENAI_API_KEY:
        ai_problems.append(
            Line("OPENAI_API_KEY is empty in .env: the AI cannot speak, callers go to a person.")
        )
    for ai in {entry.ai_agent for entry in centre.ai_entries if entry.ai_agent}:
        if not ai.active or ai.deleted:
            ai_problems.append(Line(f"{ai.name} is switched off.", change_url(ai)))
        if not ai.routes.exists():
            ai_problems.append(
                Line(f"{ai.name} has no routes: it cannot hand a caller to any queue.", change_url(ai))
            )
    listing = ", ".join(
        f"{entry.number} → {entry.ai_agent.name + ' → ' if entry.ai_agent else ''}{entry.queue.name}"
        for entry in centre.entries
    )
    return Section(
        "Entry numbers",
        "The numbers callers dial, and where each one goes.",
        [
            item(
                "entries.exist",
                "Callers have a number to dial",
                done=listing + ".",
                todo=None if centre.entries else "No entry numbers yet.",
                fix=Line("Entry points", list_url(EntryPoint)),
                manual="manual-entries",
            ),
            item(
                "entries.reach_algodial",
                "Every number reaches AlgoDial and an active queue",
                wrong,
                done="Every number is in 800 to 899 and goes to an active queue.",
                todo=None if centre.entries else "No entry numbers yet.",
                fix=Line("Entry points", list_url(EntryPoint)),
                manual="manual-entries",
            ),
            Check(
                "entries.ai",
                "AI numbers are ready",
                NOT_USED,
                "No number has an AI agent.",
                manual="manual-ai",
            )
            if not centre.ai_entries
            else item(
                "entries.ai",
                "AI numbers are ready",
                ai_problems,
                done="The AI agents are on, have routes, and the OpenAI key is set.",
                fix=Line("AI agents", list_url(AiAgent)),
                manual="manual-ai",
            ),
        ],
    )


def teams_section(centre: Centre) -> Section:
    incomplete = [
        Line(
            f"{team.name} has no "
            + " and no ".join(
                part
                for part, there in (
                    ("supervisor", team.supervisors.exists()),
                    ("agents", team.agents.exists()),
                )
                if not there
            )
            + ".",
            change_url(team),
        )
        for team in centre.teams
        if not team.supervisors.exists() or not team.agents.exists()
    ]
    in_team = {user.pk for team in centre.teams for user in team.agents.all()}
    outside = [
        Line(f"{agent} is in no team, so no supervisor sees them.", list_url(Team))
        for agent in centre.agents
        if agent.user_id and agent.user_id not in in_team
    ]
    no_teams = None if centre.teams else "No teams yet."
    return Section(
        "Teams and supervisors",
        "A supervisor sees, listens to and helps the agents of their teams.",
        [
            item(
                "teams.complete",
                "Every team has a supervisor and agents",
                incomplete,
                done=f"{len(centre.teams)} team{'s' * (len(centre.teams) != 1)}.",
                todo=no_teams,
                fix=Line("Teams", list_url(Team)),
                manual="manual-teams",
            ),
            item(
                "teams.every_agent",
                "Every agent is in a team",
                outside,
                done="Every agent is watched by a supervisor.",
                todo=no_teams,
                fix=Line("Teams", list_url(Team)),
                manual="manual-teams",
            ),
        ],
    )


def _placeholder(account: SipAccount) -> bool:
    try:
        password = decrypt_secret(account.password_encrypted)
    except Exception:  # noqa: BLE001 - an unreadable secret is not this page's to report
        return False
    plain = password.lower()
    return "changeme" in plain or plain in {"password", account.sip_id.lower()}


def phones_section() -> Section:
    softphones = list(SipAccount.objects.filter(kind="SOFTPHONE", active=True).order_by("sip_id"))
    weak = [
        Line(
            f"{account.sip_id} uses a placeholder password: set a new one, then type it in the phone.",
            change_url(account),
        )
        for account in softphones
        if _placeholder(account)
    ]
    weak += [
        Line(
            f"{account.sip_id} has a browser phone's name: the agent or supervisor of that name cannot "
            "sign in until it is renamed.",
            change_url(account),
        )
        for account in softphones
        if account.sip_id.lower().startswith(BROWSER_PREFIX)
    ]
    phones = (
        Check(
            "phones.softphones",
            "Desk phones have real passwords",
            NOT_USED,
            "No desk phones: agents use the browser phone, made for them at sign-in.",
            fix=Line("SIP accounts", list_url(SipAccount)),
            manual="manual-phones",
        )
        if not softphones
        else item(
            "phones.softphones",
            "Desk phones have real passwords",
            weak,
            done="Desk phones: "
            + ", ".join(f"{a.sip_id} {a.display_name}".strip() for a in softphones)
            + ".",
            fix=Line("SIP accounts", list_url(SipAccount)),
            manual="manual-phones",
        )
    )
    on_desk = list(Agent.objects.filter(active=True, phone__isnull=False).select_related("phone"))
    off = [
        Line(
            f"{agent} takes calls on phone {agent.phone.sip_id}, which is switched off: they cannot sign in.",
            change_url(agent),
        )
        for agent in on_desk
        if agent.phone and (not agent.phone.active or agent.phone.deleted)
    ]
    sharing: dict[str, list[str]] = {}
    for agent in on_desk:
        if agent.phone:
            sharing.setdefault(agent.phone.sip_id, []).append(str(agent))
    shared = [
        Line(f"Phone {sip_id} is shared by {', '.join(names)}: only one of them can be signed in at a time.")
        for sip_id, names in sharing.items()
        if len(names) > 1
    ]
    desk = (
        Check(
            "phones.agents",
            "Agents' desk phones are switched on",
            NOT_USED,
            "No agent has a desk phone: every agent uses the browser phone.",
            manual="manual-agents",
        )
        if not on_desk
        else item(
            "phones.agents",
            "Agents' desk phones are switched on",
            off,
            shared,
            done=", ".join(f"{agent.number} on {agent.phone.sip_id}" for agent in on_desk if agent.phone)
            + ".",
            fix=Line("Agents", list_url(Agent)),
            manual="manual-agents",
        )
    )
    return Section(
        "Phones",
        "Desk and test phones (SIP accounts). Agents' browser phones need nothing.",
        [phones, desk, carriers_check()],
    )


def carriers_check() -> Check:
    """M4b: carriers. DIDs need a live trunk to arrive; a route needs a live trunk to dial out."""
    from apps.queues.models import EntryPoint
    from apps.telephony.models import OutboundRoute, Trunk

    live = list(Trunk.objects.filter(active=True))
    dids = list(
        EntryPoint.objects.filter(active=True, number__startswith="+").values_list("number", flat=True)
    )
    routes = list(OutboundRoute.objects.filter(active=True).prefetch_related("links__trunk"))
    if not live and not dids and not routes:
        return Check(
            "phones.carriers",
            "Carriers are connected",
            NOT_USED,
            "No carriers: calls come only from phones inside the centre.",
            manual="manual-carriers",
        )
    problems = []
    if dids and not live:
        problems.append(Line(f"DIDs {', '.join(dids)} have no live trunk to arrive on.", list_url(Trunk)))
    for route in routes:
        if not any(link.trunk.active and not link.trunk.deleted for link in route.links.all()):
            problems.append(
                Line(
                    f"Route {route.name} has no live trunk: its numbers cannot be called.", change_url(route)
                )
            )
    from apps.telephony import health

    for name, state in health.trunk_health().items():
        if state.reachable == "offline":
            problems.append(
                Line(
                    f"Trunk {name} is not reachable: Asterisk's checks of the carrier get no answer.",
                    list_url(Trunk),
                )
            )
        if state.registration not in (None, "Registered", "unknown"):
            problems.append(
                Line(f"Trunk {name} is not signed in to its carrier ({state.registration}).", list_url(Trunk))
            )
    used = {link.trunk_id for route in routes for link in route.links.all()}
    notes = [
        Line(f"Trunk {trunk.name} is in no outbound route: it only takes calls in.", change_url(trunk))
        for trunk in live
        if trunk.pk not in used
    ]
    return item(
        "phones.carriers",
        "Carriers are connected",
        problems,
        notes,
        done=f"{len(live)} trunk{'s' * (len(live) != 1)}, {len(dids)} DID{'s' * (len(dids) != 1)}, "
        f"{len(routes)} outbound route{'s' * (len(routes) != 1)}.",
        fix=Line("Trunks", list_url(Trunk)),
        manual="manual-carriers",
    )


def tests_section(centre: Centre) -> Section:
    since = centre.since

    def latest(rows, describe: Callable, key, title, todo, *, manual, used=True, fix=None) -> Check:
        if not used:
            return Check(key, title, NOT_USED, "Not used here.", manual=manual)
        row = rows.first()
        if row is None:
            return Check(key, title, TODO, todo, manual=manual, fix=fix)
        text, url = describe(row)
        return Check(key, title, DONE, text, fix=Line("See it", url) if url else fix, manual=manual)

    calls_url = list_url(Call)
    answered_missing = []
    last: datetime | None = None  # the latest answered test call
    for entry in centre.entries:
        call = (
            Call.objects.filter(entry=entry.number, started_at__gte=since, result__in=ANSWERED)
            .order_by("-started_at")
            .first()
        )
        if call is None or call.started_at is None:
            answered_missing.append(Line(f"{entry.number}: no answered call in the last 30 days.", ""))
        elif last is None or call.started_at > last:
            last = call.started_at
    if not centre.entries:
        answered = Check(
            "tests.answered",
            "A test call to each number was answered",
            TODO,
            "Add an entry number first.",
            manual="manual-testing",
        )
    elif answered_missing or last is None:
        answered = Check(
            "tests.answered",
            "A test call to each number was answered",
            TODO,
            "Call each number below from a phone and answer it.",
            notes=answered_missing,
            fix=Line("Calls", calls_url),
            manual="manual-testing",
        )
    else:
        answered = Check(
            "tests.answered",
            "A test call to each number was answered",
            DONE,
            f"Every number answered; last on {when(last)}.",
            fix=Line("Calls", calls_url),
            manual="manual-testing",
        )
    session = AgentSession.objects.filter(login_at__gte=since).select_related("agent").order_by("-login_at")
    events = Event.objects.filter(at__gte=since).order_by("-at")
    return Section(
        "Tests: does it work?",
        "Ticked by themselves from the call records of the last 30 days. Do each one once after setting up, "
        "and again after a big change.",
        [
            latest(
                session,
                lambda s: (f"{s.agent} signed in on {when(s.login_at)}.", ""),
                "tests.signed_in",
                "An agent signed in on the agent page",
                "Sign in at /agent/ as an agent and press Go Ready.",
                manual="manual-testing",
            ),
            answered,
            latest(
                events.filter(type="wrapup.done"),
                lambda e: (f"Last on {when(e.at)}.", ""),
                "tests.wrapup",
                "A call was wrapped up with a code",
                "After a test call, choose a wrap-up code and finish.",
                manual="manual-testing",
            ),
            latest(
                Call.objects.filter(started_at__gte=since, recording_status="ready").order_by("-started_at"),
                lambda c: (f"Last on {when(c.started_at)}: play it on the call's page.", change_url(c)),
                "tests.recording",
                "A call recording plays",
                "Make a test call; its recording appears on the call's page (needs apply.sh m7).",
                manual="manual-testing",
                fix=Line("Calls", calls_url),
            ),
            latest(
                events.filter(type="transfer.completed"),
                lambda e: (f"Last on {when(e.at)}.", ""),
                "tests.transfer",
                "A call was transferred to a colleague",
                "During a test call, transfer it to another agent who is Ready.",
                manual="manual-testing",
            ),
            latest(
                events.filter(type="supervise.started"),
                lambda e: (f"Last on {when(e.at)}.", ""),
                "tests.supervision",
                "A supervisor listened to a call",
                "On the supervisor page, choose a call and press Monitor.",
                manual="manual-testing",
            ),
            latest(
                InteractionPart.objects.filter(kind="ai", outcome="handoff", started_at__gte=since)
                .select_related("call")
                .order_by("-started_at"),
                lambda p: (f"Last on {when(p.started_at)}.", change_url(p.call)),
                "tests.ai_handoff",
                "The AI handed a caller to a person",
                "Call an AI number, ask a question, then ask for a person.",
                manual="manual-ai",
                used=bool(centre.ai_entries),
            ),
        ],
    )


def hands_on_section() -> Section:
    from apps.setup.models import SetupConfirmation

    rows = {row.test: row for row in SetupConfirmation.objects.select_related("created_by")}
    checks = []
    for key, (title, how) in HANDS_ON.items():
        row = rows.get(key)
        if row is None:
            checks.append(
                Check(key, title, TODO, "Not confirmed yet.", hands_on=True, how=how, manual="manual-testing")
            )
            continue
        who = row.created_by.get_full_name() or row.created_by.email if row.created_by else "someone"
        summary = f"Confirmed by {who} on {when(row.created_at)}." + (f" {row.note}" if row.note else "")
        checks.append(Check(key, title, DONE, summary, hands_on=True, how=how, manual="manual-testing"))
    return Section(
        "Tests you confirm",
        "No record can prove these: try each one, then press Confirm it works.",
        checks,
    )


READ = object()  # build()'s default: ask Redis now


def build(*, now: datetime | None = None, consumers=READ) -> list[Section]:
    """Every section of the checklist, in the order a centre is set up. ``consumers``: the services'
    check-ins as :func:`read_consumers` gives them (tests pass their own)."""
    centre = Centre(now or timezone.now())
    if consumers is READ:
        consumers = read_consumers()
    return [
        services_section(centre, consumers),
        people_section(centre),
        agents_section(centre),
        queues_section(centre),
        entries_section(centre),
        teams_section(centre),
        phones_section(),
        tests_section(centre),
        hands_on_section(),
    ]


def find(sections: list[Section], key: str) -> Check:
    return next(check for section in sections for check in section.checks if check.key == key)


def progress(sections: list[Section]) -> tuple[int, int, int]:
    """(done, counted, needing attention); checks for features not used are not counted."""
    checks = [check for section in sections for check in section.checks if check.status != NOT_USED]
    return (
        sum(check.status == DONE for check in checks),
        len(checks),
        sum(check.status == ATTENTION for check in checks),
    )
