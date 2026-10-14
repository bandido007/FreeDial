"""Handoff rules the worker applies to the caller's words before asking the language model.

Rules decide what must not depend on a model: a caller who asks for a person gets one, and a
sensitive topic goes to a person at once. Matching is on whole words, case and accents ignored,
in Swahili and English.
"""

import re
import unicodedata

ASKED_FOR_PERSON = "asked_for_person"
SENSITIVE_TOPIC = "sensitive_topic"
FAILED_TURNS = "failed_turns"
AI_DECIDED = "ai_decided"
HANDOFF_REASONS = (ASKED_FOR_PERSON, AI_DECIDED, FAILED_TURNS, SENSITIVE_TOPIC)

PERSON_PHRASES = (
    # English
    "talk to a person",
    "speak to a person",
    "speak with a person",
    "talk to someone",
    "speak to someone",
    "talk to a human",
    "speak to a human",
    "real person",
    "human being",
    "an agent",
    "the agent",
    "live agent",
    "operator",
    "representative",
    "customer care",
    "customer service",
    "talk to somebody",
    # Swahili
    "nataka mtu",
    "kuongea na mtu",
    "kuzungumza na mtu",
    "niongee na mtu",
    "nizungumze na mtu",
    "mtu halisi",
    "binadamu",
    "wakala",
    "mhudumu",
    "afisa",
    "huduma kwa wateja",
    "niunganishe",
    "nipe mtu",
)

SENSITIVE_DEFAULTS = (
    # English
    "suicide",
    "kill myself",
    "end my life",
    "hurt myself",
    "rape",
    "abuse",
    "violence",
    "emergency",
    "dying",
    # Swahili
    "kujiua",
    "kujidhuru",
    "ubakaji",
    "kubakwa",
    "unyanyasaji",
    "vurugu",
    "dharura",
    "nakufa",
    "anakufa",
)


def normalise(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9']+", plain.lower()))


def readable(text: str) -> bool:
    """Swahili and English are written in Latin letters. A transcript mostly in another script (seen:
    Georgian, for a Swahili caller) means speech-to-text guessed the wrong language: not understood."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    latin = sum(1 for ch in letters if "LATIN" in unicodedata.name(ch, ""))
    return latin / len(letters) >= 0.8


def _says(text: str, phrases: tuple[str, ...]) -> str | None:
    said = f" {normalise(text)} "
    return next((phrase for phrase in phrases if f" {normalise(phrase)} " in said), None)


def asks_for_person(text: str) -> bool:
    return _says(text, PERSON_PHRASES) is not None


def sensitive(text: str, topics: tuple[str, ...] = ()) -> str | None:
    """The sensitive topic named in ``text``, if any (the agent's own list, then the defaults)."""
    return _says(text, tuple(topics) + SENSITIVE_DEFAULTS)
