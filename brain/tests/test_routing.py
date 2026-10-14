"""docs/ROUTING_DESIGN.md section 4: tests T1-T12 on the demo centre (brain/sim/demo.py).

T1-T9, T11 and T12 run the brain's core; T10 (the admin's language floor) and the seed
are Django tests (apps/queues/tests/test_routing_rules.py, brain/tests/test_seed_demo.py).
All agents are Ready and Idle unless stated; "idle for" decides longest_idle.
"""

import itertools
from dataclasses import replace

from brain.core import actions as a
from brain.core.agents import Activity
from brain.core.brain import Brain
from brain.core.config import EntryConfig
from brain.core.routing import Ticket, match, skill_score
from brain.sim import demo
from contracts import messages as m

NAMES = {number: name for name, number, *_ in demo.AGENTS}
SKILLS = {demo.agent_uid(name): levels for name, _n, _t, levels, _q in demo.AGENTS}


def names(uids):
    return {uid.removeprefix("agent-").title() for uid in uids}


def matching(queue, language, waited, free=None):
    """The pure matcher: which free members of ``queue`` may take a ``language`` call after ``waited`` s."""
    config = demo.core_config()
    queue_config = config.queues[demo.queue_uid(queue)]
    free = [demo.agent_uid(name) for name in free] if free is not None else list(queue_config.members)
    result = match(queue_config, Ticket(queue=queue_config.uid, language=language), waited, SKILLS, free)
    return names(result.agents), result


class Centre:
    """The demo centre's brain. ``ready``: who is Ready, and at which second (default: everyone at 0)."""

    def __init__(self, ready=None):
        config = demo.core_config()
        entries = dict(config.entries)
        for (
            queue
        ) in config.queues.values():  # a number per queue and language, so tests can put calls anywhere
            for language in ("swahili", "english"):
                entries[f"{queue.uid}-{language}"] = EntryConfig(
                    f"{queue.uid}-{language}", queue.uid, language, None, "general"
                )
        self.config = replace(config, entries=entries)
        ids = itertools.count(1)
        self.core = Brain(self.config, app="algodial", new_id=lambda: f"id{next(ids)}")
        ready = {name: 0.0 for name in NAMES.values()} if ready is None else ready
        for name, number, *_ in demo.AGENTS:
            uid = demo.agent_uid(name)
            self.core.on_command(m.AGENT_LOGIN, {"agent": uid, "endpoint": f"SIM/{number}"}, "c", 0)
            if name in ready:
                self.core.on_command(m.AGENT_READY, {"agent": uid}, "c", ready[name])
        self.count = 0

    def call(self, entry, now):
        self.count += 1
        cid = f"c{self.count}"
        event = {
            "type": "StasisStart",
            "args": ["inbound", entry, cid, cid],
            "channel": {"id": cid, "caller": {"name": "Mteja", "number": "0712"}},
        }
        actions = self.core.on_ari(event, now)
        return cid, rung(actions), actions

    def busy(self, *agents, now=0.0):
        for name in agents:
            self.core.on_command(
                m.AGENT_NOT_READY, {"agent": demo.agent_uid(name), "reason": "busy"}, "c", now
            )

    def ready(self, name, now):
        return self.core.on_command(m.AGENT_READY, {"agent": demo.agent_uid(name)}, "c", now)


def rung(actions):
    return [NAMES[x.endpoint.split("/")[1]] for x in actions if isinstance(x, a.Originate)]


def emitted(actions, kind):
    return [x.data for x in actions if isinstance(x, a.Emit) and x.type == kind]


def timer(actions, prefix):
    found = [x for x in actions if isinstance(x, a.SetTimer) and x.key.startswith(prefix)]
    return found[0] if found else None


# T1 ────────────────────────────────────────────────────────────────────────
def test_t1_step_one_of_claims_picks_the_longest_idle_of_neema_and_halima():
    assert matching("Claims", "swahili", 0)[0] == {"Neema", "Halima"}, "Juma, Peter, Zawadi fail step 1"
    centre = Centre(ready={**{name: 0 for name in NAMES.values()}, "Neema": 0, "Halima": 60})
    _, offered, _ = centre.call("claims-swahili", now=100)  # Neema idle 100 s, Halima 40 s
    assert offered == ["Neema"]


# T2 ────────────────────────────────────────────────────────────────────────
def test_t2_at_45_seconds_step_two_matches_juma_peter_and_zawadi():
    free = ["Juma", "Peter", "Zawadi"]  # Neema and Halima busy
    assert matching("Claims", "swahili", 45, free=free)[0] == {"Juma", "Peter", "Zawadi"}
    centre = Centre(ready={"Juma": 10, "Peter": 0, "Zawadi": 20})
    cid, offered, actions = centre.call("claims-swahili", now=100)
    assert offered == [], "step 1 needs claims >= 7 and swahili >= 6: Peter has swahili 5"
    assert rung(centre.core.on_timer(timer(actions, "widen:").key, now=130)) == ["Peter"], (
        "longest idle of the three"
    )


# T3 ────────────────────────────────────────────────────────────────────────
def test_t3_an_english_call_in_claims_matches_neema_halima_and_peter():
    assert matching("Claims", "english", 0)[0] == {"Neema", "Halima", "Peter"}, (
        "Juma english 5, Zawadi english 4"
    )


# T4 ────────────────────────────────────────────────────────────────────────
def test_t4_juma_is_not_offered_at_29_seconds_but_is_at_30():
    assert matching("Claims", "swahili", 29, free=["Juma"])[0] == set()
    assert matching("Claims", "swahili", 30, free=["Juma"])[0] == {"Juma"}, "at exactly 30 s step 1 has ended"
    centre = Centre(ready={"Juma": 0})
    cid, offered, actions = centre.call("claims-swahili", now=1000)
    widen = timer(actions, "widen:")
    assert offered == [] and widen.seconds == 30
    assert rung(centre.core.on_timer(widen.key, now=1030)) == ["Juma"]


# T5 ────────────────────────────────────────────────────────────────────────
def test_t5_best_skilled_offers_escalations_to_mussa():
    _, result = matching("Escalations", "swahili", 0, free=["Zawadi", "Mussa"])
    assert names(result.agents) == {"Zawadi", "Mussa"}
    ticket = Ticket(queue="escalations", language="swahili")
    assert round(skill_score(SKILLS[demo.agent_uid("Zawadi")], result.step, ticket), 2) == 8.0
    assert round(skill_score(SKILLS[demo.agent_uid("Mussa")], result.step, ticket), 2) == 8.67
    centre = Centre(ready={"Zawadi": 0, "Mussa": 50})  # Zawadi idle longer: best skill still wins
    assert centre.call("escalations-swahili", now=100)[1] == ["Mussa"]


# T6 ────────────────────────────────────────────────────────────────────────
def test_t6_escalations_never_drops_its_requirements_and_ends_in_voicemail_with_a_callback():
    assert matching("Escalations", "swahili", 10_000, free=["Neema"])[0] == set(), "Neema has no tier2"
    centre = Centre(ready={"Neema": 0})
    cid, offered, actions = centre.call("escalations-swahili", now=0)
    assert offered == [] and timer(actions, "widen:") is None, "R_ESCALATIONS never loosens"

    at_120 = centre.core.on_timer(f"maxwait:{cid}", now=120)
    assert emitted(at_120, m.CALL_VOICEMAIL)[0]["queue"] == "Escalations"
    [prompt] = [x for x in at_120 if isinstance(x, a.Play)]
    assert (prompt.channel, prompt.media) == (cid, "sound:vm-intro")

    done = {
        "type": "PlaybackFinished",
        "playback": {"id": prompt.playback_id, "target_uri": f"channel:{cid}", "state": "done"},
    }
    [record] = [x for x in centre.core.on_ari(done, 128) if isinstance(x, a.Record)]
    assert record.channel == cid and record.beep

    finished = {
        "type": "RecordingFinished",
        "recording": {"name": record.name, "duration": 7, "state": "done", "target_uri": f"channel:{cid}"},
    }
    end = centre.core.on_ari(finished, 140)
    [callback] = emitted(end, m.CALLBACK_REQUESTED)
    assert (callback["queue"], callback["recording"], callback["duration"], callback["caller"]) == (
        "Escalations",
        record.name,
        7,
        "0712",
    )
    assert emitted(end, m.CALL_ENDED)[0]["result"] == "voicemail"
    assert any(isinstance(x, a.Hangup) and x.channel == cid for x in end)


def test_t6_a_caller_who_hangs_up_during_the_prompt_still_gets_a_callback_task():
    centre = Centre(ready={})
    cid, _, _ = centre.call("escalations-swahili", now=0)
    centre.core.on_timer(f"maxwait:{cid}", now=120)
    gone = centre.core.on_ari({"type": "StasisEnd", "channel": {"id": cid}}, 125)
    [callback] = emitted(gone, m.CALLBACK_REQUESTED)
    assert callback["recording"] is None and emitted(gone, m.CALL_ENDED)[0]["result"] == "voicemail"


# T7 ────────────────────────────────────────────────────────────────────────
def test_t7_halima_takes_the_higher_priority_providers_call():
    centre = Centre(ready={})
    claims, _, _ = centre.call("claims-swahili", now=0)  # priority 5, waiting 50 s at the end
    providers, _, _ = centre.call("801", now=45)  # priority 10, waiting 5 s
    assert matching("Claims", "swahili", 50, free=["Halima"])[0] == {"Halima"}
    assert matching("Providers", "swahili", 5, free=["Halima"])[0] == {"Halima"}
    offered = emitted(centre.ready("Halima", now=50), m.CALL_OFFERED)
    assert [offer["call"] for offer in offered] == [providers]


# T8 ────────────────────────────────────────────────────────────────────────
def test_t8_rehema_never_gets_an_english_call_even_in_the_last_step():
    assert matching("Membership", "english", 200, free=["Rehema"])[0] == set()
    centre = Centre(ready={"Rehema": 0})
    cid, offered, actions = centre.call("membership-english", now=0)
    seen = list(offered)
    for second in (30, 90):
        actions = centre.core.on_timer(f"widen:{cid}", now=second)
        seen += rung(actions)
    assert seen == [] and timer(actions, "widen:") is None, "the last step is forever: no more widening"


# T9 ────────────────────────────────────────────────────────────────────────
def test_t9_an_ai_tag_that_is_not_configured_is_rejected_and_the_call_goes_to_general():
    centre = Centre(ready={})
    cid, _, _ = centre.call("800", now=0)
    centre.core.on_command(m.ROUTING_UPDATE, {"call": cid, "source": "ai", "queue": "Claims"}, "c1", 1)
    assert centre.core.calls[cid].queue == "claims"
    actions = centre.core.on_command(
        m.ROUTING_UPDATE, {"call": cid, "source": "ai", "queue": "Claims", "skills": {"fraud": 1}}, "c2", 2
    )
    [rejected] = emitted(actions, m.ROUTING_AI_TAG_REJECTED)
    assert rejected["rejected"] == {"skills": ["fraud"]} and rejected["source"] == "ai"
    assert centre.core.calls[cid].queue == "general", "back to the entry point's fallback"


def test_t9_a_valid_ai_ticket_is_applied_and_logged():
    centre = Centre(ready={})
    cid, _, _ = centre.call("800", now=0)
    actions = centre.core.on_command(
        m.ROUTING_UPDATE,
        {"call": cid, "source": "ai", "queue": "Claims", "language": "english", "skills": {"claims": 5}},
        "c",
        3,
    )
    [ticket] = emitted(actions, m.ROUTING_TICKET)
    assert (ticket["queue"], ticket["language"], ticket["skills"], ticket["source"]) == (
        "Claims",
        "english",
        {"claims": 5},
        "ai",
    )


# T11 ───────────────────────────────────────────────────────────────────────
def test_t11_an_agent_who_rings_out_is_not_responding_and_the_next_matching_agent_rings():
    centre = Centre(ready={"Neema": 0, "Halima": 10})
    cid, offered, _ = centre.call("claims-swahili", now=20)
    assert offered == ["Neema"]
    leg = next(iter(centre.core.calls[cid].offers))
    after = centre.core.on_ari({"type": "ChannelDestroyed", "cause": 19, "channel": {"id": leg}}, 35)
    assert centre.core.agents[demo.agent_uid("Neema")].activity is Activity.NOT_RESPONDING
    assert rung(after) == ["Halima"]


# T12 ───────────────────────────────────────────────────────────────────────
def test_t12_raising_jumas_claims_applies_at_once():
    centre = Centre(ready={"Juma": 0})
    cid, offered, _ = centre.call("claims-swahili", now=0)
    assert offered == []
    agents = dict(centre.config.agents)
    juma = agents[demo.agent_uid("Juma")]
    agents[juma.uid] = replace(juma, skills={**juma.skills, "claims": 7})
    assert rung(centre.core.load_config(replace(centre.config, agents=agents), now=10)) == ["Juma"]
