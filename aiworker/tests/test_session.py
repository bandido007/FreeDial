"""M8: one AI conversation, with the call's media and OpenAI faked.

The session greets, listens (turns by voice activity), answers through the language model, and
ends its part with a command to the brain: ai.handoff (with the summary and transcript) or
ai.hangup. The caller can interrupt it (barge-in), and rules hand off without asking the model.
"""

import asyncio
import functools
import math
import struct
from typing import Any

from aiworker.core.audio import FRAME_BYTES
from aiworker.core.conversation import SAY_AGAIN
from aiworker.core.settings import AgentSettings, Route
from aiworker.service import Session
from contracts import messages as m

SETTINGS = AgentSettings(
    uid="ai-1",
    name="Jambo AI",
    instructions="A health insurance helpline.",
    greetings={"swahili": "Habari, karibu.", "english": "Hello, welcome."},
    routes=(Route("Claims", "claims", {"claims": 1}), Route("General", "anything else")),
)

LOUD = struct.pack("<480h", *(int(9000 * math.sin(2 * math.pi * 300 * n / 24000)) for n in range(480)))
QUIET = b"\x00" * FRAME_BYTES


def scenario(test):
    """Run an async test body on a fresh event loop, with a time limit."""

    @functools.wraps(test)
    def run(*args, **kwargs):
        asyncio.run(asyncio.wait_for(test(*args, **kwargs), 10))

    return run


def talking(frames: int = 20) -> list[bytes]:
    """The caller talks for ``frames`` x 20 ms, then is quiet long enough to end the turn."""
    return [LOUD] * frames + [QUIET] * 40


class FakeMedia:
    """Asterisk's side of the WebSocket: what the caller says in, what the AI says out."""

    def __init__(self, play_seconds: float = 0.01):
        self.inbox: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self.audio: list[bytes] = []
        self.marks: list[str] = []
        self.flushes = 0
        self.play_seconds = play_seconds  # how long Asterisk takes to play what was sent

    async def events(self):
        while True:
            event = await self.inbox.get()
            yield event
            if event[0] == "closed":
                return

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.append(pcm)

    async def mark(self, name: str) -> None:
        self.marks.append(name)
        if self.play_seconds is not None:
            asyncio.get_running_loop().call_later(self.play_seconds, self.inbox.put_nowait, ("mark", name))

    async def flush(self) -> None:
        self.flushes += 1

    def caller_says(self, frames: list[bytes]) -> None:
        for frame in frames:
            self.inbox.put_nowait(("audio", frame))


class FakeSpeech:
    """OpenAI: scripted transcriptions and tool choices; records what the AI was made to say."""

    def __init__(self, heard: list[str], choices: list[tuple[str, dict]]):
        self.heard, self.choices = list(heard), list(choices)
        self.spoken: list[str] = []
        self.asked: list[dict[str, Any]] = []

    async def transcribe(self, pcm, *, model, language=None, rate=24000, prompt=""):
        return self.heard.pop(0)

    async def decide(self, *, model, messages, tools, tool_choice="required"):
        self.asked.append({"messages": messages, "tool_choice": tool_choice})
        choice = self.choices.pop(0)
        if isinstance(choice, Exception):
            raise choice
        return choice

    async def speak(self, text, *, model, voice, style=""):
        self.spoken.append(text)
        yield QUIET * 3
        yield QUIET * 2


class Commands:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def __call__(self, kind: str, data: dict) -> None:
        await asyncio.sleep(0.01)  # Redis takes a moment: the session must not cancel its own send
        self.sent.append((kind, data))


def session(media, speech, commands, language="swahili", settings=SETTINGS):
    return Session(
        call="1791990000.1",
        leg="ai-leg-1",
        language=language,
        settings=settings,
        media=media,
        speech=speech,
        send=commands,
    )


async def run(coroutine, timeout=5):
    return await asyncio.wait_for(coroutine, timeout)


async def until(condition, timeout=3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "never happened"
        await asyncio.sleep(0.005)


@scenario
async def test_greets_answers_one_question_and_hands_off_on_request():
    media, commands = FakeMedia(), Commands()
    speech = FakeSpeech(
        heard=["Ofisi zinafunguliwa saa ngapi?", "Nataka kuongea na mtu tafadhali."],
        choices=[
            ("reply", {"text": "Tunafungua saa mbili asubuhi.", "language": "swahili"}),
            (
                "handoff",
                {
                    "reason": "ai_decided",
                    "queue": "General",
                    "summary": "Asked opening hours; wants a person.",
                    "say": "Subiri, nakuunganisha.",
                    "language": "swahili",
                },
            ),
        ],
    )
    task = asyncio.create_task(session(media, speech, commands).run())
    media.inbox.put_nowait(("start", {"format": "slin24", "optimal_frame_size": 960}))
    await until(lambda: speech.spoken == ["Habari, karibu."])
    media.caller_says(talking(20))
    await until(lambda: len(speech.spoken) == 2)
    assert speech.spoken[1] == "Tunafungua saa mbili asubuhi."
    media.caller_says(talking(20))
    assert await run(task) == "handoff"

    assert speech.spoken[2] == "Subiri, nakuunganisha."
    assert speech.asked[1]["tool_choice"] == {"type": "function", "function": {"name": "handoff"}}, (
        "the rule decided"
    )
    [(kind, data)] = commands.sent
    assert kind == m.AI_HANDOFF and data["call"] == "1791990000.1"
    assert data["reason"] == "asked_for_person", (
        "a caller asking for a person is a rule, whatever the model says"
    )
    assert (data["queue"], data["language"], data["skills"]) == ("General", "swahili", {})
    assert data["summary"] == "Asked opening hours; wants a person."
    assert [turn["who"] for turn in data["transcript"]] == ["ai", "caller", "ai", "caller", "ai"]
    assert media.marks and media.audio, "the AI's words went to Asterisk"


@scenario
async def test_the_caller_can_interrupt_the_ai():
    media, commands = FakeMedia(play_seconds=None), Commands()  # nothing finishes playing by itself
    speech = FakeSpeech(
        heard=["Habari"],
        choices=[("end_call", {"summary": "Greeted.", "say": "Kwaheri.", "language": "swahili"})],
    )
    task = asyncio.create_task(session(media, speech, commands).run())
    media.inbox.put_nowait(("start", {}))
    await until(lambda: speech.spoken == ["Habari, karibu."])
    media.caller_says(talking(20))  # talks over the greeting
    await until(lambda: media.flushes == 1)
    await until(lambda: len(speech.spoken) == 2)
    media.inbox.put_nowait(("mark", media.marks[-1]))  # the goodbye finished playing
    assert await run(task) == "resolved"
    assert commands.sent[0][0] == m.AI_HANGUP and commands.sent[0][1]["summary"] == "Greeted."


@scenario
async def test_two_failed_turns_hand_off():
    media, commands = FakeMedia(), Commands()
    speech = FakeSpeech(
        heard=["", ""], choices=[("handoff", {"summary": "Could not understand.", "say": "Subiri."})]
    )
    task = asyncio.create_task(session(media, speech, commands).run())
    media.inbox.put_nowait(("start", {}))
    media.caller_says(talking(20))
    await until(lambda: len(speech.spoken) == 2)
    assert speech.spoken[1] == SAY_AGAIN["swahili"]
    media.caller_says(talking(20))
    assert await run(task) == "handoff"
    assert commands.sent[0][1]["reason"] == "failed_turns"


@scenario
async def test_a_sensitive_topic_goes_to_a_person_at_once():
    media, commands = FakeMedia(), Commands()
    speech = FakeSpeech(
        heard=["I want to kill myself"], choices=[("reply", {"text": "I can help.", "language": "english"})]
    )
    task = asyncio.create_task(session(media, speech, commands, language="english").run())
    media.inbox.put_nowait(("start", {}))
    media.caller_says(talking(20))
    assert await run(task) == "handoff"
    data = commands.sent[0][1]
    assert data["reason"] == "sensitive_topic" and data["summary"]
    assert speech.spoken[-1].startswith("Please hold on"), "the model's reply is not used: the rule speaks"


@scenario
async def test_when_openai_fails_a_person_takes_over():
    media, commands = FakeMedia(), Commands()
    speech = FakeSpeech(
        heard=["Hello", "Hello?"], choices=[RuntimeError("503"), RuntimeError("503"), RuntimeError("503")]
    )
    task = asyncio.create_task(session(media, speech, commands, language="english").run())
    media.inbox.put_nowait(("start", {}))
    media.caller_says(talking(20))
    await until(lambda: len(speech.spoken) == 2)
    media.caller_says(talking(20))
    assert await run(task) == "handoff"
    data = commands.sent[0][1]
    assert data["reason"] == "failed_turns" and data["summary"].startswith("Caller said: Hello")


@scenario
async def test_the_caller_hangs_up():
    media, commands = FakeMedia(), Commands()
    task = asyncio.create_task(session(media, FakeSpeech([], []), commands).run())
    media.inbox.put_nowait(("start", {}))
    media.inbox.put_nowait(("closed", None))
    assert await run(task) == "closed"
    assert commands.sent == []


class SlowVoice(FakeSpeech):
    """gpt-4o-mini-tts as seen for half an hour on 2026-10-14: speech starts after many seconds."""

    def __init__(self):
        super().__init__([], [])
        self.models: list[str] = []

    async def speak(self, text, *, model, voice, style=""):
        self.models.append(f"{model}/{voice}")
        if model == "gpt-4o-mini-tts":
            await asyncio.sleep(30)
        self.spoken.append(text)
        yield QUIET


@scenario
async def test_a_voice_slow_to_start_is_replaced_by_tts_1():
    import aiworker.service as service

    service.FIRST_AUDIO_SECONDS, saved = 0.05, service.FIRST_AUDIO_SECONDS
    try:
        media, commands, speech = FakeMedia(), Commands(), SlowVoice()
        settings = AgentSettings(**{**SETTINGS.__dict__, "voice": "marin"})
        task = asyncio.create_task(session(media, speech, commands, settings=settings).run())
        media.inbox.put_nowait(("start", {}))
        await until(lambda: speech.spoken == ["Habari, karibu."])
        assert speech.models == ["gpt-4o-mini-tts/marin", "tts-1/coral"], "marin is not a tts-1 voice: coral"
        assert media.audio == [QUIET]
        media.inbox.put_nowait(("closed", None))
        assert await run(task) == "closed"

        # the next call does not wait for the slow voice again
        media, again = FakeMedia(), SlowVoice()
        task = asyncio.create_task(session(media, again, commands, settings=settings).run())
        media.inbox.put_nowait(("start", {}))
        await until(lambda: again.spoken == ["Habari, karibu."])
        assert again.models == ["tts-1/coral"]
        media.inbox.put_nowait(("closed", None))
        await run(task)
    finally:
        service.FIRST_AUDIO_SECONDS = saved
        service.slow_until.clear()


class StallingVoice(SlowVoice):
    """gpt-4o-mini-tts as measured on 2026-10-15: starts on time, then goes silent for 14 s."""

    async def speak(self, text, *, model, voice, style=""):
        self.models.append(f"{model}/{voice}")
        self.spoken.append(text)
        yield LOUD
        if model == "gpt-4o-mini-tts":
            await asyncio.sleep(30)
            yield LOUD


@scenario
async def test_a_voice_that_stalls_mid_sentence_is_flushed_and_said_again_by_tts_1():
    import aiworker.service as service

    saved = service.STALL_SECONDS
    service.STALL_SECONDS = 0.05
    try:
        media, commands, speech = FakeMedia(), Commands(), StallingVoice()
        task = asyncio.create_task(session(media, speech, commands).run())
        media.inbox.put_nowait(("start", {}))
        await until(lambda: len(speech.models) == 2)
        await until(lambda: len(media.audio) == 2)
        assert speech.models == ["gpt-4o-mini-tts/coral", "tts-1/coral"]
        assert media.flushes == 1, "the half sentence is dropped before it is said again"
        media.inbox.put_nowait(("closed", None))
        await run(task)
    finally:
        service.STALL_SECONDS = saved
        service.slow_until.clear()
