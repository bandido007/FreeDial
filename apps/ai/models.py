"""AI agents (M8): who answers an entry point before a person does, and where it may send callers.

The brain only needs to know an entry point has an AI agent (it opens the media leg and waits for
the worker). The AI worker loads the rest at the start of each call: instructions, greetings,
routes, voice and models. Changes reach the next call without a restart (rule 10).
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel

# The models each part may use. Defaults: the bake-off's winners (docs/AI_BAKEOFF.md).
STT_MODELS = [
    ("gpt-4o-transcribe", "gpt-4o-transcribe"),
    ("gpt-4o-mini-transcribe", "gpt-4o-mini-transcribe"),
    ("whisper-1", "whisper-1"),
]
LLM_MODELS = [
    (name, name)
    for name in ("gpt-4o", "gpt-5-mini", "gpt-4.1-mini", "gpt-4.1", "gpt-5.4-mini-2026-03-17", "gpt-5.2")
]
TTS_MODELS = [("gpt-4o-mini-tts", "gpt-4o-mini-tts"), ("tts-1-hd", "tts-1-hd"), ("tts-1", "tts-1")]
VOICES = [
    (name, name.title())
    for name in (
        "coral",
        "marin",
        "cedar",
        "alloy",
        "ash",
        "ballad",
        "echo",
        "fable",
        "nova",
        "onyx",
        "sage",
        "shimmer",
        "verse",
    )
]
DEFAULT_STT, DEFAULT_LLM, DEFAULT_TTS, DEFAULT_VOICE = (
    "gpt-4o-transcribe",
    "gpt-4o",
    "gpt-4o-mini-tts",
    "coral",
)


class AiAgent(BaseModel):
    name = models.CharField(max_length=80, help_text="What the AI calls itself, e.g. Msaidizi")
    instructions = models.TextField(
        help_text="Who the helpline is and what the AI may answer. Everything it says comes from here."
    )
    greeting_swahili = models.TextField(blank=True, help_text="First words on a Swahili entry point")
    greeting_english = models.TextField(blank=True, help_text="First words on an English entry point")
    sensitive_topics = models.TextField(
        blank=True,
        help_text="One per line: words that send the caller to a person at once (added to the built-in list)",
    )
    max_failed_turns = models.PositiveSmallIntegerField(
        default=2,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Turns in a row the AI may fail to understand before a person takes over",
    )
    start_timeout = models.PositiveSmallIntegerField(
        default=10,
        validators=[MinValueValidator(2), MaxValueValidator(60)],
        help_text="Seconds for the AI to come on the line; after that the call goes to a person",
    )
    voice = models.CharField(max_length=20, choices=VOICES, default=DEFAULT_VOICE)
    stt_model = models.CharField(
        "speech-to-text model", max_length=60, choices=STT_MODELS, default=DEFAULT_STT
    )
    llm_model = models.CharField("language model", max_length=60, choices=LLM_MODELS, default=DEFAULT_LLM)
    tts_model = models.CharField(
        "text-to-speech model", max_length=60, choices=TTS_MODELS, default=DEFAULT_TTS
    )

    class Meta:
        db_table = "ai_agents"
        ordering = ["name"]
        verbose_name = "AI agent"
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=Q(deleted=False), name="uniq_live_ai_agent_name"
            ),
        ]

    def __str__(self):
        return self.name

    def topics(self) -> list[str]:
        return [line.strip() for line in self.sensitive_topics.splitlines() if line.strip()]


class AiRoute(models.Model):
    """A queue the AI may send callers to, when, and the topic skill the call then needs."""

    ai_agent = models.ForeignKey(AiAgent, on_delete=models.CASCADE, related_name="routes")
    queue = models.ForeignKey("queues.Queue", on_delete=models.CASCADE, related_name="ai_routes")
    when = models.CharField(max_length=200, help_text="In words, for the AI: e.g. claims, payments, benefits")
    skill = models.ForeignKey(
        "agents.Skill",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={"kind": "topic", "deleted": False},
        help_text="The topic skill the call needs in that queue (optional)",
    )
    min_level = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(10)]
    )
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "ai_routes"
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["ai_agent", "queue"], name="uniq_ai_route_queue")]

    def __str__(self):
        return f"{self.ai_agent} -> {self.queue}"
