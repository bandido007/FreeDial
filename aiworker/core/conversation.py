"""One caller's conversation with the AI agent: what was said, and what to do next.

The worker hands the caller's words to :meth:`Conversation.heard`. Rules come first (a person
asked for, a sensitive topic, failed turns); otherwise the language model chooses one tool:
``reply``, ``clarify`` (it did not understand), ``handoff`` (to a queue, with a summary for the
agent) or ``end_call``. :meth:`decided` turns that choice into a :class:`Decision`.
"""

from dataclasses import dataclass, field
from typing import Any

from aiworker.core import rules
from aiworker.core.settings import AgentSettings

LANGUAGES = ("swahili", "english")

HOLD_ON = {
    "swahili": "Tafadhali subiri kidogo, nakuunganisha na mhudumu sasa hivi.",
    "english": "Please hold on, I'm connecting you to a person now.",
}
SAY_AGAIN = {
    "swahili": "Samahani, sikusikia vizuri. Unaweza kurudia tafadhali?",
    "english": "Sorry, I didn't catch that. Could you say it again, please?",
}
GOODBYE = {"swahili": "Asante kwa kupiga simu. Kwaheri.", "english": "Thank you for calling. Goodbye."}


@dataclass(frozen=True)
class Say:
    text: str


@dataclass(frozen=True)
class Clarify:
    """The AI did not understand: ask again (a failed turn)."""

    text: str


@dataclass(frozen=True)
class Summarize:
    """A rule decided a handoff: ask the model only for the summary and the words to the caller."""

    reason: str


@dataclass(frozen=True)
class Handoff:
    reason: str
    summary: str
    say: str
    queue: str = ""
    language: str = ""
    skills: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Hangup:
    reason: str
    summary: str
    say: str


Decision = Say | Clarify | Summarize | Handoff | Hangup


class Conversation:
    def __init__(self, settings: AgentSettings, language: str | None):
        self.settings = settings
        self.language = language if language in LANGUAGES else "english"
        self.transcript: list[dict[str, Any]] = []
        self.failed = 0  # failed turns in a row

    # ── what happens ──────────────────────────────────────────────────────
    def heard(self, text: str, at: float) -> Decision | None:
        """The caller's words (empty: nothing understood). A decision, or None: ask the model."""
        text = text.strip()
        if not text or not rules.readable(text):
            return self._failed_turn(SAY_AGAIN[self.language])
        self.transcript.append({"who": "caller", "text": text, "at": round(at, 1)})
        if rules.asks_for_person(text):
            return Summarize(rules.ASKED_FOR_PERSON)
        if rules.sensitive(text, self.settings.sensitive_topics):
            return Summarize(rules.SENSITIVE_TOPIC)
        return None

    def said(self, text: str, at: float) -> None:
        self.transcript.append({"who": "ai", "text": text, "at": round(at, 1)})

    def decided(self, tool: str, args: dict[str, Any], forced_reason: str = "") -> Decision:
        """The model's choice as a decision. ``forced_reason``: a rule already chose the handoff."""
        language = str(args.get("language") or "")
        if language in LANGUAGES:
            self.language = language
        text = str(args.get("text") or args.get("say") or "").strip()
        if forced_reason or tool == "handoff":
            return self.handoff(forced_reason or self._reason(args), args)
        if tool == "end_call":
            return Hangup(
                rules.AI_DECIDED if not args.get("reason") else str(args["reason"]),
                str(args.get("summary") or self._fallback_summary()),
                text or GOODBYE[self.language],
            )
        if tool == "clarify" or not text:
            return self._failed_turn(text or SAY_AGAIN[self.language])
        self.failed = 0
        return Say(text)

    def handoff(self, reason: str, args: dict[str, Any] | None = None) -> Handoff:
        """A handoff with what the model gave (or what we know, when it gave nothing)."""
        args = args or {}
        queue = str(args.get("queue") or "")
        route = next((route for route in self.settings.routes if route.queue == queue), None)
        return Handoff(
            reason=reason,
            summary=str(args.get("summary") or "").strip() or self._fallback_summary(),
            say=str(args.get("say") or "").strip() or HOLD_ON[self.language],
            queue=queue,
            language=self.language,
            skills=dict(route.skills) if route else {},
        )

    def _failed_turn(self, text: str) -> Decision:
        self.failed += 1
        if self.failed >= self.settings.max_failed_turns:
            return Summarize(rules.FAILED_TURNS)
        return Clarify(text)

    def _reason(self, args: dict[str, Any]) -> str:
        reason = str(args.get("reason") or "")
        return reason if reason in rules.HANDOFF_REASONS else rules.AI_DECIDED

    def _fallback_summary(self) -> str:
        words = [turn["text"] for turn in self.transcript if turn["who"] == "caller"][-3:]
        return "Caller said: " + " / ".join(words) if words else "The caller said nothing the AI understood."

    # ── what the model sees ───────────────────────────────────────────────
    def messages(self, forced_reason: str = "") -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt()}]
        for turn in self.transcript:
            out.append({"role": "user" if turn["who"] == "caller" else "assistant", "content": turn["text"]})
        if forced_reason:
            out.append(
                {
                    "role": "system",
                    "content": f"The caller is now handed to a person (reason: {forced_reason}). Call handoff. "
                    "The summary tells the agent everything the caller asked or needs in this call, what you "
                    "already told them, and why a person takes over. Then one short sentence telling the "
                    "caller to hold on.",
                }
            )
        return out

    def system_prompt(self) -> str:
        s = self.settings
        routes = (
            "\n".join(f"   - {route.queue}: {route.when}" for route in s.routes) or "   - (the default queue)"
        )
        return f"""You are {s.name}, the voice assistant answering phone calls for this helpline:
{s.instructions.strip()}

How you work:
- This is a phone call. Answer in the caller's language, Swahili or English (the call started in {self.language});
  switch when the caller does.
- Keep every reply short: one or two plain sentences. No lists, no markdown, no emojis: it is spoken aloud.
- Answer only from the helpline information above. If you do not know, or the caller needs something done
  on their account (a claim, a payment, a card), hand the call to a person.
- Call exactly one tool every turn:
  reply: answer, or ask one short question.
  clarify: you could not understand what the caller said.
  handoff: the caller should talk to a person. Choose the queue that fits:
{routes}
  end_call: the caller's need is met and they are done.
- Never invent queues, policies, amounts or phone numbers.
- A handoff summary is for the agent, in English: who is calling, what they need, what you already told them."""

    def tools(self) -> list[dict[str, Any]]:
        queues = [route.queue for route in self.settings.routes]
        language = {
            "type": "string",
            "enum": list(LANGUAGES),
            "description": "The language the caller speaks now",
        }

        def tool(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": list(properties),
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            }

        text = {"type": "string", "description": "What to say to the caller, in their language"}
        handoff: dict[str, Any] = {
            "reason": {
                "type": "string",
                "enum": [rules.ASKED_FOR_PERSON, rules.AI_DECIDED, rules.SENSITIVE_TOPIC],
            },
            "summary": {
                "type": "string",
                "description": "For the agent, in English, one to three sentences: what the caller asked or needs, "
                "what you already told them, why a person takes over",
            },
            "say": {
                "type": "string",
                "description": "One short sentence to the caller: hold on for a person",
            },
            "language": language,
        }
        if queues:
            handoff["queue"] = {"type": "string", "enum": queues}
        return [
            tool("reply", "Say this to the caller.", {"text": text, "language": language}),
            tool(
                "clarify",
                "You could not understand the caller; ask them to repeat.",
                {"text": text, "language": language},
            ),
            tool("handoff", "Hand the caller to a person in a queue.", handoff),
            tool(
                "end_call",
                "The caller's need is met: say goodbye and end the call.",
                {"summary": {"type": "string"}, "say": text, "language": language},
            ),
        ]
