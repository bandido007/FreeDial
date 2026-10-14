"""The AI worker: one :class:`Session` per call, each in its own task (rule 7: calls never wait on each other).

A session greets the caller, then loops: the caller's audio goes through the turn detector; a
finished turn is transcribed, checked by the rules, and given to the language model, whose choice
is spoken back. The caller may talk over the AI (barge-in: what is still queued in Asterisk is
flushed). The session ends its part with one command to the brain, sent once the last words
have been played: ai.handoff or ai.hangup. Nothing here changes the call itself (rule 2).
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from aiworker.core.audio import FRAME_BYTES, seconds
from aiworker.core.conversation import (
    Clarify,
    Conversation,
    Decision,
    Handoff,
    Hangup,
    Say,
    Summarize,
)
from aiworker.core.rules import SENSITIVE_TOPIC
from aiworker.core.settings import AgentSettings
from aiworker.core.turns import SpeechStarted, TurnDetector, TurnEnded
from contracts import messages as m

log = logging.getLogger("aiworker")

# The bake-off (docs/AI_BAKEOFF.md) found the language hint does not help: callers switch.
STT_LANGUAGE_HINT = False
# On 2026-10-14 gpt-4o-mini-tts took 17-30 s to start speaking for half an hour; tts-1 kept answering in
# about a second. If the chosen voice has not started within this, the sentence is spoken by tts-1.
FIRST_AUDIO_SECONDS = 3.0
FALLBACK_TTS = "tts-1"
FALLBACK_VOICES = ("alloy", "ash", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer")
LAST_WORDS_GRACE = 5.0  # seconds past the last words' length to wait for Asterisk to have played them


class Media(Protocol):
    def events(self) -> AsyncIterator[tuple[str, Any]]: ...
    async def send_audio(self, pcm: bytes) -> None: ...
    async def mark(self, name: str) -> None: ...
    async def flush(self) -> None: ...


class Speech(Protocol):
    async def transcribe(
        self, pcm: bytes, *, model: str, language: str | None = None, rate: int = 24000, prompt: str = ""
    ) -> str: ...
    async def decide(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: Any = "required",
    ) -> tuple[str, dict[str, Any]]: ...
    def speak(self, text: str, *, model: str, voice: str, style: str = "") -> AsyncIterator[bytes]: ...


class Session:
    def __init__(
        self,
        *,
        call: str,
        leg: str,
        language: str | None,
        settings: AgentSettings,
        media: Media,
        speech: Speech,
        send: Callable[[str, dict[str, Any]], Awaitable[Any]],
        clock: Callable[[], float] = time.monotonic,
    ):
        self.call, self.leg, self.settings = call, leg, settings
        self.media, self.speech, self.send, self.clock = media, speech, send, clock
        self.conversation = Conversation(settings, language)
        self.detector = TurnDetector()
        self.turns: asyncio.Queue[bytes] = asyncio.Queue()
        self.started = clock()
        self.buffer = b""
        self.marks = 0
        self.playing: str | None = None  # the mark of words still playing in Asterisk
        self.final: tuple[str, str, dict[str, Any]] | None = None  # (mark, command, data): sent once played
        self.done = asyncio.Event()
        self.outcome = "closed"
        self.last_words = 0.0  # seconds of the last thing said
        self.timer: asyncio.Task[None] | None = None

    # ── the call's audio ──────────────────────────────────────────────────
    async def run(self) -> str:
        """Until the caller or the brain closes the media, or the AI's part is over. Returns how it ended."""
        reader = asyncio.create_task(self._read())
        thinker = asyncio.create_task(self._think())
        over = asyncio.create_task(self.done.wait())
        try:
            await asyncio.wait({reader, over}, return_when=asyncio.FIRST_COMPLETED)
            if reader.done() and reader.exception() is not None:
                log.error("call %s: media failed: %s", self.call, reader.exception())
        finally:
            for task in (reader, thinker, over, self.timer):
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        if not self.done.is_set():
            self.outcome = "closed"  # the caller hung up (or the brain took the leg) before the AI finished
        return self.outcome

    async def _read(self) -> None:
        async for kind, payload in self.media.events():
            if kind == "start":
                log.info("call %s: AI on the line (%s)", self.call, payload)
                await self._say(self.settings.greeting(self.conversation.language))
            elif kind == "audio":
                await self._heard(payload)
            elif kind == "mark":
                await self._played(str(payload))
            elif kind == "closed":
                return

    async def _heard(self, pcm: bytes) -> None:
        self.buffer += pcm
        while len(self.buffer) >= FRAME_BYTES:
            frame, self.buffer = self.buffer[:FRAME_BYTES], self.buffer[FRAME_BYTES:]
            for event in self.detector.feed(frame):
                if isinstance(event, SpeechStarted) and self.playing and self.final is None:
                    self.playing = None  # barge-in: stop talking, listen
                    await self.media.flush()
                elif isinstance(event, TurnEnded) and self.final is None:
                    self.turns.put_nowait(event.pcm)

    async def _played(self, mark: str) -> None:
        if mark == self.playing:
            self.playing = None
        if self.final is not None and mark == self.final[0]:
            await self._finish()

    # ── one turn at a time ────────────────────────────────────────────────
    async def _think(self) -> None:
        while self.final is None:
            pcm = await self.turns.get()
            decision: Decision | None
            try:
                decision = await self._decide(pcm)
            except Exception:  # OpenAI down or confused: a failed turn; enough and a person takes over
                log.exception("call %s: turn failed", self.call)
                decision = self.conversation.heard("", self.clock() - self.started)
                if isinstance(decision, Summarize):
                    decision = await self._summarize(decision.reason)
            if decision is not None:
                await self._act(decision)

    async def _decide(self, pcm: bytes) -> Decision:
        hint = self.conversation.language if STT_LANGUAGE_HINT else None
        text = await self.speech.transcribe(pcm, model=self.settings.stt_model, language=hint)
        log.info("call %s: caller said %r (%.1f s)", self.call, text, seconds(pcm))
        decision = self.conversation.heard(text, self.clock() - self.started)
        if decision is None:
            name, args = await self.speech.decide(
                model=self.settings.llm_model,
                messages=self.conversation.messages(),
                tools=self.conversation.tools(),
            )
            decision = self.conversation.decided(name, args)
        if isinstance(decision, Summarize):
            decision = await self._summarize(decision.reason)
        return decision

    async def _summarize(self, reason: str) -> Handoff:
        """A rule chose the handoff; the model writes the summary and the words to the caller."""
        try:
            _name, args = await self.speech.decide(
                model=self.settings.llm_model,
                messages=self.conversation.messages(forced_reason=reason),
                tools=self.conversation.tools(),
                tool_choice={"type": "function", "function": {"name": "handoff"}},
            )
        except Exception:
            log.exception("call %s: no summary from the model", self.call)
            return self.conversation.handoff(reason)
        if reason == SENSITIVE_TOPIC:
            args = {**args, "say": ""}  # a sensitive topic: our own careful words, not the model's
        decision = self.conversation.decided("handoff", args, forced_reason=reason)
        assert isinstance(decision, Handoff)
        return decision

    async def _act(self, decision: Decision) -> None:
        if isinstance(decision, Say | Clarify):
            await self._say(decision.text)
        elif isinstance(decision, Handoff):
            data = {
                "call": self.call,
                "reason": decision.reason,
                "summary": decision.summary,
                "queue": decision.queue,
                "language": decision.language,
                "skills": decision.skills,
            }
            await self._say_last(decision.say, m.AI_HANDOFF, data, "handoff")
        elif isinstance(decision, Hangup):
            data = {"call": self.call, "reason": decision.reason, "summary": decision.summary}
            await self._say_last(decision.say, m.AI_HANGUP, data, "resolved")

    # ── speaking ──────────────────────────────────────────────────────────
    async def _say(self, text: str) -> str:
        self.conversation.said(text, self.clock() - self.started)
        log.info("call %s: AI says %r", self.call, text)
        sent = 0
        async for chunk in self._voice(text):
            await self.media.send_audio(chunk)
            sent += len(chunk)
        self.marks += 1
        mark = f"say-{self.marks}"
        self.playing = mark
        await self.media.mark(mark)
        self.last_words = sent / 2 / 24000
        return mark

    async def _voice(self, text: str) -> AsyncIterator[bytes]:
        """The sentence as speech, from the chosen model; from tts-1 if that one is too slow to start."""
        s = self.settings
        chunks = self.speech.speak(text, model=s.tts_model, voice=s.voice, style=s.voice_style).__aiter__()
        try:
            first = await asyncio.wait_for(chunks.__anext__(), FIRST_AUDIO_SECONDS)
        except StopAsyncIteration:
            return
        except TimeoutError:
            log.warning("call %s: %s slow to speak, using %s", self.call, s.tts_model, FALLBACK_TTS)
            voice = s.voice if s.voice in FALLBACK_VOICES else "coral"
            async for chunk in self.speech.speak(text, model=FALLBACK_TTS, voice=voice):
                yield chunk
            return
        yield first
        async for chunk in chunks:
            yield chunk

    async def _say_last(self, text: str, command: str, data: dict[str, Any], outcome: str) -> None:
        """The AI's last words; its part ends (the command goes) once Asterisk has played them."""
        data["transcript"] = self.conversation.transcript
        self.outcome = outcome
        try:
            mark = await self._say(text)
        except Exception:
            log.exception("call %s: could not say the last words", self.call)
            self.final = ("", command, data)
            await self._finish()
            return
        data["transcript"] = self.conversation.transcript
        self.final = (mark, command, data)
        self.timer = asyncio.create_task(self._finish_later(self.last_words + LAST_WORDS_GRACE))

    async def _finish_later(self, delay: float) -> None:
        """If Asterisk never says the last words were played, the command goes anyway."""
        await asyncio.sleep(delay)
        await self._finish()

    async def _finish(self) -> None:
        if self.final is None or self.done.is_set():
            return
        self.done.set()
        _mark, command, data = self.final
        await self.send(command, data)
        log.info("call %s: AI part over: %s (%s)", self.call, command, data.get("reason"))


# ── the worker process ────────────────────────────────────────────────────
async def run_worker(key: str) -> None:
    """Read ai.started from the bus; run one session per call, each in its own task (rule 7)."""
    import aiohttp

    from aiworker.adapters import ari, config
    from aiworker.adapters.bus import Bus
    from aiworker.adapters.media import AsteriskMedia
    from aiworker.adapters.openai import OpenAi

    bus = Bus()
    await bus.join()
    sessions: dict[str, asyncio.Task[None]] = {}
    async with aiohttp.ClientSession() as http:
        speech = OpenAi(http, key)

        async def one_call(data: dict[str, Any]) -> None:
            call, leg = str(data["call"]), str(data["leg"])
            try:
                settings = await config.load(str(data["ai_agent"]))
                if settings is None:
                    log.error(
                        "call %s: AI agent %s not found or inactive; the brain hands over",
                        call,
                        data["ai_agent"],
                    )
                    return
                connection = await ari.connection_id(http, leg)
                if connection is None:
                    log.warning("call %s: media leg %s is gone", call, leg)
                    return
                media = await AsteriskMedia.connect(http, connection)
                try:
                    session = Session(
                        call=call,
                        leg=leg,
                        language=data.get("language"),
                        settings=settings,
                        media=media,
                        speech=speech,
                        send=bus.send,
                    )
                    log.info("call %s: AI %s (%s) answers", call, settings.name, data.get("language"))
                    outcome = await session.run()
                    log.info("call %s: AI session over: %s", call, outcome)
                finally:
                    await media.close()
            except Exception:  # one call failing never stops the worker; the brain hands that call over
                log.exception("call %s: AI session failed", call)
            finally:
                sessions.pop(call, None)

        log.info("AI worker ready (consumer %s)", bus.consumer)
        try:
            async for entry_id, event in bus.events():
                if event is not None and event.type == m.AI_STARTED:
                    call = str(event.data.get("call"))
                    if call not in sessions:  # at-least-once delivery: a repeat changes nothing
                        sessions[call] = asyncio.create_task(one_call(event.data))
                await bus.ack(entry_id)
        finally:
            for task in sessions.values():
                task.cancel()
            await bus.close()
