"""An AI agent's settings, as the worker needs them (loaded from the database by the adapters)."""

from dataclasses import dataclass, field

# Chosen by the bake-off (docs/AI_BAKEOFF.md): the best of each part on Swahili and English phrases
# heard through a telephone line, within a latency a phone call can bear.
STT_MODEL = "gpt-4o-transcribe"
LLM_MODEL = "gpt-4o"
TTS_MODEL = "gpt-4o-mini-tts"
VOICE = "coral"


@dataclass(frozen=True)
class Route:
    """A queue the AI may hand callers to, when (in words, for the model) and with which skills."""

    queue: str
    when: str
    skills: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentSettings:
    uid: str
    name: str
    instructions: str  # who the AI is and what it may answer: the helpline's knowledge
    greetings: dict[str, str]  # language skill (swahili, english) -> first words
    routes: tuple[Route, ...] = ()
    sensitive_topics: tuple[str, ...] = ()  # words that send the caller to a person at once
    max_failed_turns: int = 2
    voice: str = VOICE
    stt_model: str = STT_MODEL
    llm_model: str = LLM_MODEL
    tts_model: str = TTS_MODEL
    voice_style: str = "Warm, calm and clear, like a helpful call-centre agent. Speak at a natural pace."

    def greeting(self, language: str | None) -> str:
        return self.greetings.get(language or "") or next(iter(self.greetings.values()), "Hello?")
