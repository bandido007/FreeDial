"""The worker's pure parts: turn detection, handoff rules, the conversation, audio helpers."""

import io
import math
import struct
import wave

from aiworker.core import audio, rules
from aiworker.core.conversation import (
    GOODBYE,
    HOLD_ON,
    SAY_AGAIN,
    Clarify,
    Conversation,
    Handoff,
    Hangup,
    Say,
    Summarize,
)
from aiworker.core.settings import AgentSettings, Route
from aiworker.core.turns import SpeechStarted, TurnDetector, TurnEnded

SETTINGS = AgentSettings(
    uid="ai-1",
    name="Msaidizi",
    instructions="A helpline.",
    greetings={"swahili": "Habari", "english": "Hello"},
    routes=(Route("Claims", "claims, payments", {"claims": 1}), Route("General", "anything else")),
    sensitive_topics=("overdose",),
)


def frame(level: int, hz: int = 300) -> bytes:
    return struct.pack("<480h", *(int(level * math.sin(2 * math.pi * hz * n / 24000)) for n in range(480)))


LOUD, QUIET, HISS = frame(9000), b"\x00" * audio.FRAME_BYTES, frame(100, 3000)


def feed(detector, frames):
    events = []
    for f in frames:
        events += detector.feed(f)
    return events


# ── turns ─────────────────────────────────────────────────────────────────
def test_silence_and_line_noise_are_no_turn():
    assert feed(TurnDetector(), [QUIET] * 100 + [HISS] * 100) == []


def test_speech_then_a_pause_is_one_turn_with_what_came_just_before():
    detector = TurnDetector()
    events = feed(detector, [QUIET] * 20 + [LOUD] * 25 + [QUIET] * 40)
    assert isinstance(events[0], SpeechStarted) and isinstance(events[1], TurnEnded) and len(events) == 2
    turn_frames = len(events[1].pcm) // audio.FRAME_BYTES
    assert 25 + 35 <= turn_frames <= 25 + 35 + 15, "the words, the quiet that ended them, and a little before"


def test_a_click_is_not_a_turn():
    events = feed(TurnDetector(), [LOUD] * 8 + [QUIET] * 50)  # 160 ms: speech starts, but too short to keep
    assert [type(e) for e in events] == [SpeechStarted]


def test_short_pauses_inside_a_sentence_do_not_end_it():
    events = feed(TurnDetector(), ([LOUD] * 15 + [QUIET] * 10) * 3 + [QUIET] * 40)
    assert [type(e) for e in events] == [SpeechStarted, TurnEnded]


def test_a_caller_who_never_stops_is_heard_in_pieces():
    events = feed(TurnDetector(max_ms=2000), [LOUD] * 250)
    assert sum(isinstance(e, TurnEnded) for e in events) == 2


def test_the_noise_floor_follows_a_noisy_line():
    detector = TurnDetector()
    feed(detector, [frame(1500)] * 300)  # a loud hum that never stops: within seconds it is the floor
    assert detector.floor > 300 and not detector.talking
    assert feed(detector, [frame(1500)] * 100) == [], "the hum is no longer speech"
    assert isinstance(feed(detector, [frame(9000)] * 10)[0], SpeechStarted), "words above it still are"


# ── rules ─────────────────────────────────────────────────────────────────
def test_asking_for_a_person_in_swahili_and_english():
    for said in (
        "Nataka kuongea na mtu tafadhali",
        "NIUNGANISHE na mhudumu!",
        "Can I speak to a person?",
        "I need an agent",
    ):
        assert rules.asks_for_person(said), said
    for said in ("Mtu wangu amepoteza kadi", "Personal details", "my agent number", "Reagent"):
        assert not rules.asks_for_person(said), said


def test_sensitive_topics_default_and_per_agent():
    assert rules.sensitive("Nataka kujiua") == "kujiua"
    assert rules.sensitive("He took an overdose", ("overdose",)) == "overdose"
    assert rules.sensitive("emergency room bill") == "emergency"
    assert rules.sensitive("My card is lost") is None


def test_normalise_drops_case_accents_and_punctuation():
    assert rules.normalise("Habári, MTU!  halisi?") == "habari mtu halisi"


# ── conversation ──────────────────────────────────────────────────────────
def test_rules_come_before_the_model():
    conversation = Conversation(SETTINGS, "swahili")
    assert conversation.heard("Ofisi zinafunguliwa lini?", 1.0) is None, "ask the model"
    assert conversation.heard("nataka kuongea na mtu", 2.0) == Summarize(rules.ASKED_FOR_PERSON)
    assert Conversation(SETTINGS, "english").heard("I took an overdose", 1.0) == Summarize(
        rules.SENSITIVE_TOPIC
    )


def test_failed_turns_in_a_row():
    conversation = Conversation(SETTINGS, "english")
    assert conversation.heard("", 1.0) == Clarify(SAY_AGAIN["english"])
    assert conversation.decided("reply", {"text": "Sure."}) == Say("Sure."), "a good turn resets the count"
    assert conversation.decided("clarify", {"text": "Pardon?"}) == Clarify("Pardon?")
    assert conversation.heard("   ", 3.0) == Summarize(rules.FAILED_TURNS)


def test_a_handoff_takes_the_routes_skills_and_the_callers_language():
    conversation = Conversation(SETTINGS, "swahili")
    conversation.heard("Madai yangu hayajalipwa", 1.0)
    decision = conversation.decided(
        "handoff",
        {
            "reason": "ai_decided",
            "queue": "Claims",
            "summary": "Unpaid claim.",
            "say": "Subiri.",
            "language": "swahili",
        },
    )
    assert decision == Handoff("ai_decided", "Unpaid claim.", "Subiri.", "Claims", "swahili", {"claims": 1})


def test_a_handoff_without_words_still_has_a_summary_and_a_hold_on():
    conversation = Conversation(SETTINGS, "english")
    conversation.heard("My claim", 1.0)
    conversation.heard("is not paid", 2.0)
    decision = conversation.decided("handoff", {"reason": "made up"}, forced_reason=rules.FAILED_TURNS)
    assert decision.reason == rules.FAILED_TURNS and decision.say == HOLD_ON["english"]
    assert (
        decision.summary == "Caller said: My claim / is not paid"
        and decision.queue == ""
        and decision.skills == {}
    )


def test_end_call_and_language_switch():
    conversation = Conversation(SETTINGS, "swahili")
    conversation.decided("reply", {"text": "Sure, how can I help?", "language": "english"})
    assert conversation.language == "english"
    assert conversation.decided("end_call", {"summary": "Asked hours."}) == Hangup(
        "ai_decided", "Asked hours.", GOODBYE["english"]
    )


def test_what_the_model_sees():
    conversation = Conversation(SETTINGS, "swahili")
    conversation.said("Habari", 0.0)
    conversation.heard("Nina swali", 1.5)
    messages = conversation.messages(forced_reason=rules.ASKED_FOR_PERSON)
    assert (
        messages[0]["role"] == "system"
        and "Msaidizi" in messages[0]["content"]
        and "- Claims: claims, payments" in messages[0]["content"]
    )
    assert [msg["role"] for msg in messages[1:]] == ["assistant", "user", "system"]
    tools = {tool["function"]["name"]: tool["function"] for tool in conversation.tools()}
    assert set(tools) == {"reply", "clarify", "handoff", "end_call"}
    assert tools["handoff"]["parameters"]["properties"]["queue"]["enum"] == ["Claims", "General"]
    assert conversation.transcript == [
        {"who": "ai", "text": "Habari", "at": 0.0},
        {"who": "caller", "text": "Nina swali", "at": 1.5},
    ]


def test_an_unknown_language_starts_in_english():
    assert Conversation(SETTINGS, "french").language == "english"
    assert SETTINGS.greeting("french") == "Habari", "no greeting in that language: the first one"


# ── audio ─────────────────────────────────────────────────────────────────
def test_wav_and_loudness():
    with wave.open(io.BytesIO(audio.wav(LOUD))) as file:
        assert (file.getnchannels(), file.getframerate(), file.getnframes()) == (1, 24000, 480)
    assert audio.rms(QUIET) == 0 and 6000 < audio.rms(LOUD) < 6500 and audio.rms(b"") == 0
    assert audio.seconds(LOUD * 50) == 1.0


def test_a_phone_line_keeps_speech_band_and_drops_the_rest():
    voice, whistle = frame(9000, 1000) * 10, frame(9000, 9000) * 10
    line_voice, line_whistle = audio.telephone_line(voice), audio.telephone_line(whistle)
    assert len(line_voice) == len(voice) // 3, "24 kHz in, 8 kHz out"
    assert audio.rms(line_voice) > 5000 and audio.rms(line_whistle) < 1500


def test_a_transcript_in_another_script_is_not_understood():
    assert rules.readable("Habari, nataka kujua") and rules.readable("Ofisi ya Jumamosi?")
    assert (
        not rules.readable("მინამიდამიაგურს ცუდული ვიყავი")
        and not rules.readable("...")
        and not rules.readable("")
    )
    conversation = Conversation(SETTINGS, "swahili")
    assert conversation.heard("მინამიდამიაგურს ცუდული", 1.0) == Clarify(SAY_AGAIN["swahili"])
    assert conversation.transcript == [], "what was not understood is not put in the transcript"
