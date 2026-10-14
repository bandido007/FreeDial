# AI agent bake-off (M8)

Run on 2026-10-14 with `manage.py ai_bakeoff`. **Synthetic callers**: 20 phrases (10 Swahili,
10 English) spoken by `gpt-4o-mini-tts` (voices coral and ash, told to speak like Tanzanian callers) and passed through a telephone line
(band-limited, 8 kHz G.711 mu-law), as Asterisk hears a caller. A first ranking: re-run on real
recordings (M7 keeps every call in stereo) before going live.

## Chosen

| Part | Model | Why |
| --- | --- | --- |
| Speech-to-text | `gpt-4o-transcribe` | 12/12 spoken turns decided right end to end (with `gpt-4o`), 1.2% of letters wrong, median 0.94 s |
| Language model | `gpt-4o` | 14/14 right tool and queue, 14/14 right language, median 1.01 s (fastest within one point of the best) |
| Text-to-speech | `gpt-4o-mini-tts` | its words survive the phone line (1.5% WER) and it starts speaking soonest of the best (1.10 s) |

## Speech-to-text (20 phrases through a phone line)

Character error rate (CER, spaces ignored) is the fairer measure for Swahili, which joins words:
"Madai yangu" heard as "Madayangu" is two wrong words but one wrong letter. End to end: the
transcript of each spoken caller turn given to `gpt-4o`; is the decision still right?

| Model | Language hint | End to end | WER Swahili | CER Swahili | WER English | CER all | Not Latin script | Median latency |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gpt-4o-transcribe` | no | 12/12 | 9.5% | 2.0% | 1.1% | 1.2% | 0 | 0.94 s |
| `gpt-4o-transcribe` | yes | 12/12 | 10.8% | 3.3% | 1.1% | 1.9% | 0 | 0.89 s |
| `whisper-1` | yes | 12/12 | 37.8% | 3.5% | 1.1% | 1.9% | 0 | 1.56 s |
| `gpt-4o-mini-transcribe` | yes | 12/12 | 30.2% | 4.4% | 1.1% | 2.4% | 0 | 0.84 s |
| `gpt-4o-mini-transcribe` | no | 12/12 | 37.6% | 6.3% | 1.1% | 3.3% | 0 | 0.76 s |
| `whisper-1` | no | 11/12 | 40.7% | 4.8% | 1.1% | 2.6% | 0 | 1.33 s |

## Language model (14 caller turns: answer, clarify, hand off to the right queue, end)

| Model | Right tool and queue | Swahili answered in Swahili (or English turn) | Median latency | Choices |
| --- | --- | --- | --- | --- |
| `gpt-4o` | 14/14 | 14/14 | 1.01 s | reply, reply, handoff Claims, handoff Providers, handoff Escalations, end_call, clarify, reply, reply, handoff Claims, handoff Providers, handoff Escalations, end_call, clarify |
| `gpt-5-mini` | 14/14 | 14/14 | 1.18 s | reply, reply, handoff Claims, handoff Providers, handoff Escalations, end_call, clarify, reply, reply, handoff Claims, handoff Providers, handoff Escalations, end_call, clarify |
| `gpt-4.1-mini` | 14/14 | 14/14 | 1.20 s | reply, reply, handoff Claims, handoff Providers, handoff Escalations, end_call, clarify, reply, reply, handoff Claims, handoff Providers, handoff Escalations, end_call, clarify |
| `gpt-5.2` | 14/14 | 14/14 | 2.06 s | reply, reply, handoff Claims, handoff Providers, handoff Escalations, end_call, clarify, reply, reply, handoff Claims, handoff Providers, handoff Escalations, end_call, clarify |
| `gpt-4.1` | 14/14 | 13/14 | 1.08 s | reply, reply, handoff Claims, handoff Providers, handoff Escalations, end_call, clarify, reply, reply, handoff Claims, handoff Providers, handoff Escalations, end_call, clarify |
| `gpt-5.4-mini-2026-03-17` | 14/14 | 13/14 | 1.18 s | reply, reply, handoff Claims, handoff Providers, handoff Escalations, end_call, clarify, reply, reply, handoff Claims, handoff Providers, handoff Escalations, end_call, clarify |

Expected: reply, reply, handoff Claims, handoff Providers, handoff Escalations, end_call, clarify, reply, reply, handoff Claims, handoff Providers, handoff Escalations, end_call, clarify

## Text-to-speech (2 Swahili and 2 English sentences)

| Model | First audio (median) | Whole sentence (median) | WER after the phone line |
| --- | --- | --- | --- |
| `gpt-4o-mini-tts` | 1.10 s | 4.01 s | 1.5% |
| `tts-1-hd` | 2.02 s | 3.80 s | 1.5% |
| `tts-1` | 1.29 s | 3.28 s | 4.8% |

Seen on 2026-10-14: for about half an hour `gpt-4o-mini-tts` took 17 to 30 s to start speaking while
`tts-1` answered in about 1 s. The worker therefore falls back to `tts-1` for any sentence whose
first audio takes over 3 s.

The synthetic callers are `gpt-4o-mini-tts` told to sound like a Swahili speaker from Dar es Salaam;
they are still not real callers. The phrase audio is in `lab/bakeoff/` (not in git) to listen to.
