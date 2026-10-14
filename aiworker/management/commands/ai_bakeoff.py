"""The M8 bake-off: which OpenAI model is best for each part of the AI agent, on this helpline.

    manage.py ai_bakeoff                 # writes docs/AI_BAKEOFF.md, saves the phrase audio in lab/bakeoff/

20 caller phrases (10 Swahili, 10 English) are spoken by OpenAI's text-to-speech in two voices and
passed through a telephone line (band-limited, 8 kHz G.711 mu-law), as Asterisk would hear them.
They are synthetic, not real callers: a first ranking, to be re-run on real recordings.

* Speech-to-text: word error rate (WER) and latency of each model, with and without a language hint.
* Language model: on 14 caller turns, does it pick the right tool (reply, clarify, handoff with the
  right queue, end_call) and answer Swahili in Swahili; and how fast.
* Text-to-speech: time to the first audio, and whether its words survive the phone line (WER
  of the best speech-to-text on it).
"""

import asyncio
import os
import statistics
import time
from datetime import date
from pathlib import Path

import aiohttp
from django.conf import settings
from django.core.management.base import BaseCommand

from aiworker.adapters.openai import OpenAi
from aiworker.core.audio import telephone_line, wav
from aiworker.core.conversation import Conversation
from aiworker.core.rules import normalise, readable
from aiworker.core.settings import AgentSettings, Route
from apps.ai import demo

PHRASES = [
    ("swahili", "Habari, nataka kujua jinsi ya kujiandikisha kama mwanachama."),
    ("swahili", "Kadi yangu ya bima imepotea, nifanye nini?"),
    ("swahili", "Ninataka kumwongeza mtoto wangu kama mtegemezi."),
    ("swahili", "Madai yangu ya hospitali bado hayajalipwa."),
    ("swahili", "Ofisi zenu zinafunguliwa saa ngapi siku ya Jumamosi?"),
    ("swahili", "Nataka kuongea na mtu, tafadhali."),
    ("swahili", "Tunapiga simu kutoka hospitali kuhusu uthibitisho wa kituo chetu."),
    ("swahili", "Nina malalamiko kuhusu huduma niliyopata jana."),
    ("swahili", "Je, usajili ni bure au kuna malipo?"),
    ("swahili", "Asante sana, hiyo ndiyo yote."),
    ("english", "Hello, I would like to know how to register as a member."),
    ("english", "I lost my insurance card. What should I do?"),
    ("english", "How do I add my wife as a dependant?"),
    ("english", "My claim from last month has not been paid yet."),
    ("english", "What time does your office open on Saturday?"),
    ("english", "I want to talk to a person, please."),
    ("english", "I'm calling from a pharmacy about a provider claim."),
    ("english", "I want to make a complaint about how I was treated."),
    ("english", "Is registration free or do I have to pay?"),
    ("english", "Thank you, that's all I needed."),
]

# (language, what the caller says, expected tool, expected queue or "")
TURNS = [
    ("swahili", PHRASES[0][1], "reply", ""),
    ("swahili", PHRASES[1][1], "reply", ""),
    ("swahili", PHRASES[3][1], "handoff", "Claims"),
    ("swahili", PHRASES[6][1], "handoff", "Providers"),
    ("swahili", PHRASES[7][1], "handoff", "Escalations"),
    ("swahili", PHRASES[9][1], "end_call", ""),
    ("swahili", "eeh... ile... mm... sijui...", "clarify", ""),
    ("english", PHRASES[10][1], "reply", ""),
    ("english", PHRASES[14][1], "reply", ""),
    ("english", PHRASES[13][1], "handoff", "Claims"),
    ("english", PHRASES[16][1], "handoff", "Providers"),
    ("english", PHRASES[17][1], "handoff", "Escalations"),
    ("english", PHRASES[19][1], "end_call", ""),
    ("english", "the... uh... hmm, what...", "clarify", ""),
]

STT_MODELS = ["whisper-1", "gpt-4o-mini-transcribe", "gpt-4o-transcribe"]
LLM_MODELS = ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-5.4-mini-2026-03-17", "gpt-5.2", "gpt-5-mini"]
TTS_MODELS = ["gpt-4o-mini-tts", "tts-1-hd", "tts-1"]
VOICES = ["coral", "ash"]
CALLER_VOICE_MODEL = "gpt-4o-mini-tts"  # it can be told an accent; tts-1 voices read Swahili like English
CALLER_STYLE = {
    "swahili": "You are a caller on the phone. Speak Swahili like a native speaker from Dar es Salaam, at a natural pace.",
    "english": "You are a caller on the phone. Speak English with a Tanzanian accent, at a natural pace.",
}
SPOKEN = [
    ("swahili", demo.GREETING_SWAHILI),
    ("swahili", "Usajili ni bure. Tembelea ofisi yoyote ya Mfuko ukiwa na kitambulisho cha taifa."),
    ("english", demo.GREETING_ENGLISH),
    ("english", "Registration is free. Visit any Fund office with your national ID card and one photo."),
]


def _edits(ref: list[str], hyp: list[str]) -> int:
    row = list(range(len(hyp) + 1))
    for i, word in enumerate(ref, 1):
        previous, row[0] = row[0], i
        for j, other in enumerate(hyp, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (word != other))
    return row[len(hyp)]


def wer(reference: str, heard: str) -> float:
    """Word error rate: word edits (substitute, insert, delete) per reference word."""
    ref = normalise(reference).split()
    return _edits(ref, normalise(heard).split()) / max(1, len(ref))


def cer(reference: str, heard: str) -> float:
    """Character error rate, spaces ignored: fair to Swahili, where "Madai yangu" heard as "Madayangu"
    is one letter off, not two wrong words."""
    ref = list(normalise(reference).replace(" ", ""))
    return _edits(ref, list(normalise(heard).replace(" ", ""))) / max(1, len(ref))


def demo_settings() -> AgentSettings:
    return AgentSettings(
        uid="bakeoff",
        name=demo.NAME,
        instructions=demo.INSTRUCTIONS,
        greetings={"swahili": demo.GREETING_SWAHILI, "english": demo.GREETING_ENGLISH},
        routes=tuple(Route(queue, when) for queue, when, _skill, _level in demo.ROUTES),
    )


def looks_swahili(text: str) -> bool:
    words = set(normalise(text).split())
    return (
        len(words & {"na", "ya", "kwa", "ni", "wa", "tafadhali", "asante", "yako", "kama", "la", "za", "cha"})
        >= 1
    )


class Command(BaseCommand):
    help = "Rank OpenAI's speech-to-text, language and text-to-speech models for the AI agent; writes docs/AI_BAKEOFF.md."

    def add_arguments(self, parser):
        parser.add_argument("--out", default="docs/AI_BAKEOFF.md")

    def handle(self, *args, **options):
        key = os.environ.get("OPENAI_API_KEY") or getattr(settings, "OPENAI_API_KEY", "")
        if not key:
            self.stderr.write("OPENAI_API_KEY is not set (.env).")
            return
        report = asyncio.run(self.bakeoff(key))
        Path(options["out"]).write_text(report)
        self.stdout.write(report)

    async def bakeoff(self, key: str) -> str:
        audio_dir = Path(settings.BASE_DIR) / "lab" / "bakeoff"
        audio_dir.mkdir(parents=True, exist_ok=True)
        async with aiohttp.ClientSession() as http:
            ai = OpenAi(http, key)
            limit = asyncio.Semaphore(6)

            async def limited(coroutine):
                async with limit:
                    return await coroutine

            async def synthesize(index: int, text: str) -> bytes:
                language = PHRASES[index][0]
                pcm = b"".join(
                    [
                        chunk
                        async for chunk in ai.speak(
                            text,
                            model=CALLER_VOICE_MODEL,
                            voice=VOICES[index % 2],
                            style=CALLER_STYLE[language],
                            timeout=90,
                        )
                    ]
                )
                line = telephone_line(pcm)
                (audio_dir / f"phrase-{index + 1:02d}.wav").write_bytes(wav(line, 8000))
                return line

            self.stdout.write("Speaking the 20 phrases and passing them through a phone line...")
            lines = await asyncio.gather(
                *(limited(synthesize(i, text)) for i, (_l, text) in enumerate(PHRASES))
            )

            async def transcribe(model: str, hint: bool, index: int) -> tuple[float, float, float, str]:
                language, text = PHRASES[index]
                started = time.monotonic()
                heard = await ai.transcribe(
                    lines[index], model=model, language=language if hint else None, rate=8000
                )
                return wer(text, heard), cer(text, heard), time.monotonic() - started, heard

            stt: dict[tuple[str, bool], list[tuple[float, float, float, str]]] = {}
            for model in STT_MODELS:
                for hint in (False, True):
                    self.stdout.write(f"Speech-to-text: {model}{' with language hint' if hint else ''}")
                    stt[(model, hint)] = list(
                        await asyncio.gather(
                            *(limited(transcribe(model, hint, i)) for i in range(len(PHRASES)))
                        )
                    )

            agent = demo_settings()

            async def turn(model: str, case: tuple[str, str, str, str]) -> tuple[bool, bool, float, str]:
                language, said, tool, queue = case
                if not readable(said):
                    return tool == "clarify", True, 0.0, "unreadable"
                conversation = Conversation(agent, language)
                conversation.said(agent.greeting(language), 0.0)
                conversation.transcript.append({"who": "caller", "text": said, "at": 2.0})
                started = time.monotonic()
                try:
                    name, args = await ai.decide(
                        model=model, messages=conversation.messages(), tools=conversation.tools()
                    )
                except Exception as exc:  # noqa: BLE001 - a model that fails a turn scores it as wrong
                    return False, False, time.monotonic() - started, f"error: {exc}"[:80]
                took = time.monotonic() - started
                right = name == tool and (not queue or args.get("queue") == queue)
                spoken = str(args.get("text") or args.get("say") or "")
                language_ok = language != "swahili" or not spoken or looks_swahili(spoken)
                return right, language_ok, took, f"{name} {args.get('queue') or ''}".strip()

            llm: dict[str, list[tuple[bool, bool, float, str]]] = {}
            for model in LLM_MODELS:
                self.stdout.write(f"Language model: {model}")
                llm[model] = list(await asyncio.gather(*(limited(turn(model, case)) for case in TURNS)))
            chosen_llm = self.best_llm(llm)[0]

            # End to end: what each speech-to-text model heard, decided on by the chosen language model.
            spoken_turns = [
                (i, case) for case in TURNS for i, (_l, text) in enumerate(PHRASES) if text == case[1]
            ]
            e2e: dict[tuple[str, bool], int] = {}
            for stt_key, results in stt.items():
                self.stdout.write(
                    f"End to end: {stt_key[0]}{' with hint' if stt_key[1] else ''} -> {chosen_llm}"
                )
                outcomes = await asyncio.gather(
                    *(
                        limited(turn(chosen_llm, (case[0], results[i][3], case[2], case[3])))
                        for i, case in spoken_turns
                    )
                )
                e2e[stt_key] = sum(1 for ok, _l, _t, _w in outcomes if ok)
            self.spoken_turns = len(spoken_turns)

            async def speak_test(model: str, language: str, text: str) -> tuple[float, float, float]:
                started = time.monotonic()
                first, chunks = None, []
                async for chunk in ai.speak(
                    text, model=model, voice="coral", style="Warm and clear.", timeout=90
                ):
                    first = first or time.monotonic() - started
                    chunks.append(chunk)
                total = time.monotonic() - started
                heard = await ai.transcribe(
                    telephone_line(b"".join(chunks)), model="gpt-4o-transcribe", language=language, rate=8000
                )
                return first or total, total, wer(text, heard)

            tts: dict[str, list[tuple[float, float, float]]] = {}
            for model in TTS_MODELS:
                self.stdout.write(f"Text-to-speech: {model}")
                tts[model] = list(
                    await asyncio.gather(
                        *(limited(speak_test(model, language, text)) for language, text in SPOKEN)
                    )
                )
        return self.report(stt, llm, tts, e2e, chosen_llm)

    def best_llm(self, llm):
        rows = []
        for model, results in llm.items():
            right = sum(1 for ok, _l, _t, _w in results if ok)
            language_ok = sum(1 for _o, ok, _t, _w in results if ok)
            rows.append(
                (model, right, language_ok, statistics.median(t for _o, _l, t, _w in results), results)
            )
        top = max(row[1] + row[2] for row in rows)
        return min((row for row in rows if row[1] + row[2] >= top - 1), key=lambda row: row[3])

    def report(self, stt, llm, tts, e2e, chosen_llm) -> str:
        sw = [i for i, (language, _t) in enumerate(PHRASES) if language == "swahili"]
        en = [i for i, (language, _t) in enumerate(PHRASES) if language == "english"]

        def mean(values):
            return statistics.mean(values) if values else 0.0

        stt_rows = []
        for (model, hint), results in stt.items():
            words = [w for w, _c, _t, _h in results]
            chars = [c for _w, c, _t, _h in results]
            stt_rows.append(
                (
                    model,
                    hint,
                    mean([words[i] for i in sw]),
                    mean([chars[i] for i in sw]),
                    mean([words[i] for i in en]),
                    mean(chars),
                    statistics.median(t for _w, _c, t, _h in results),
                    e2e[(model, hint)],
                    sum(1 for _w, _c, _t, heard in results if not readable(heard)),
                )
            )
        # Most turns decided right end to end; then fewest letters wrong; then the fastest.
        best_stt = min(stt_rows, key=lambda row: (-row[7], round(row[5], 2), row[6]))

        llm_rows = []
        for model, results in llm.items():
            right = sum(1 for ok, _l, _t, _w in results if ok)
            language_ok = sum(1 for _o, ok, _t, _w in results if ok)
            llm_rows.append(
                (model, right, language_ok, statistics.median(t for _o, _l, t, _w in results), results)
            )
        best_llm = self.best_llm(llm)

        tts_rows = [
            (
                model,
                statistics.median(r[0] for r in results),
                statistics.median(r[1] for r in results),
                mean([r[2] for r in results]),
            )
            for model, results in tts.items()
        ]
        best_wer = min(row[3] for row in tts_rows)
        best_tts = min((row for row in tts_rows if row[3] <= best_wer + 0.02), key=lambda row: row[1])

        lines = [
            "# AI agent bake-off (M8)",
            "",
            f"Run on {date.today().isoformat()} with `manage.py ai_bakeoff`. **Synthetic callers**: 20 phrases (10 Swahili,",
            "10 English) spoken by `gpt-4o-mini-tts` (voices coral and ash, told to speak like Tanzanian callers) and passed through a telephone line",
            "(band-limited, 8 kHz G.711 mu-law), as Asterisk hears a caller. A first ranking: re-run on real",
            "recordings (M7 keeps every call in stereo) before going live.",
            "",
            "## Chosen",
            "",
            "| Part | Model | Why |",
            "| --- | --- | --- |",
            f"| Speech-to-text | `{best_stt[0]}`{' with the language hint' if best_stt[1] else ''} | {best_stt[7]}/{self.spoken_turns} spoken turns decided right end to end (with `{chosen_llm}`), {best_stt[5]:.1%} of letters wrong, median {best_stt[6]:.2f} s |",
            f"| Language model | `{best_llm[0]}` | {best_llm[1]}/{len(TURNS)} right tool and queue, {best_llm[2]}/{len(TURNS)} right language, median {best_llm[3]:.2f} s (fastest within one point of the best) |",
            f"| Text-to-speech | `{best_tts[0]}` | its words survive the phone line ({best_tts[3]:.1%} WER) and it starts speaking soonest of the best ({best_tts[1]:.2f} s) |",
            "",
            "## Speech-to-text (20 phrases through a phone line)",
            "",
            "Character error rate (CER, spaces ignored) is the fairer measure for Swahili, which joins words:",
            '"Madai yangu" heard as "Madayangu" is two wrong words but one wrong letter. End to end: the',
            f"transcript of each spoken caller turn given to `{chosen_llm}`; is the decision still right?",
            "",
            "| Model | Language hint | End to end | WER Swahili | CER Swahili | WER English | CER all | Not Latin script | Median latency |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            *(
                f"| `{m}` | {'yes' if h else 'no'} | {ok}/{self.spoken_turns} | {ws:.1%} | {cs:.1%} | {we:.1%} | {ca:.1%} | {bad} | {t:.2f} s |"
                for m, h, ws, cs, we, ca, t, ok, bad in sorted(stt_rows, key=lambda r: (-r[7], r[5]))
            ),
            "",
            f"## Language model ({len(TURNS)} caller turns: answer, clarify, hand off to the right queue, end)",
            "",
            "| Model | Right tool and queue | Swahili answered in Swahili (or English turn) | Median latency | Choices |",
            "| --- | --- | --- | --- | --- |",
            *(
                f"| `{m}` | {r}/{len(TURNS)} | {lang}/{len(TURNS)} | {t:.2f} s | {', '.join(w for _o, _l, _t, w in res)} |"
                for m, r, lang, t, res in sorted(llm_rows, key=lambda r: (-(r[1] + r[2]), r[3]))
            ),
            "",
            "Expected: " + ", ".join(f"{tool} {queue}".strip() for _l, _s, tool, queue in TURNS),
            "",
            "## Text-to-speech (2 Swahili and 2 English sentences)",
            "",
            "| Model | First audio (median) | Whole sentence (median) | WER after the phone line |",
            "| --- | --- | --- | --- |",
            *(
                f"| `{m}` | {f:.2f} s | {w:.2f} s | {e:.1%} |"
                for m, f, w, e in sorted(tts_rows, key=lambda r: r[3])
            ),
            "",
            "Seen on 2026-10-14: for about half an hour `gpt-4o-mini-tts` took 17 to 30 s to start speaking while",
            "`tts-1` answered in about 1 s. The worker therefore falls back to `tts-1` for any sentence whose",
            "first audio takes over 3 s.",
            "",
            "The synthetic callers are `gpt-4o-mini-tts` told to sound like a Swahili speaker from Dar es Salaam;",
            "they are still not real callers. The phrase audio is in `lab/bakeoff/` (not in git) to listen to.",
            "",
        ]
        return "\n".join(lines)
