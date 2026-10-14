"""Audio as the worker handles it: 16-bit little-endian mono PCM ("slin"), 24 kHz on the call.

Pure helpers (no I/O): loudness, WAV packing for speech-to-text, and the telephone-line
simulation the bake-off uses (24 kHz -> 8 kHz mu-law -> back).
"""

import io
import math
import sys
import wave
from array import array

RATE = 24000  # slin24: what OpenAI's speech models take and give
FRAME_MS = 20
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2  # 960: one 20 ms frame


def samples(pcm: bytes) -> array:
    values = array("h")
    values.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder == "big":
        values.byteswap()
    return values


def to_bytes(values: array) -> bytes:
    if sys.byteorder == "big":
        values = array("h", values)
        values.byteswap()
    return values.tobytes()


def rms(pcm: bytes) -> float:
    values = samples(pcm)
    if not values:
        return 0.0
    return math.sqrt(sum(v * v for v in values) / len(values))


def wav(pcm: bytes, rate: int = RATE) -> bytes:
    """Mono 16-bit PCM as a WAV file (what the transcription API takes)."""
    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(pcm[: len(pcm) - len(pcm) % 2])
    return out.getvalue()


def seconds(pcm: bytes, rate: int = RATE) -> float:
    return len(pcm) / 2 / rate


def _lowpass(values: array, taps: int = 31, cutoff: float = 3400 / RATE) -> list[float]:
    """A windowed-sinc low-pass filter (Hamming), enough to band-limit before decimating."""
    middle = taps // 2
    kernel = []
    for n in range(taps):
        x = n - middle
        sinc = 2 * cutoff if x == 0 else math.sin(2 * math.pi * cutoff * x) / (math.pi * x)
        kernel.append(sinc * (0.54 - 0.46 * math.cos(2 * math.pi * n / (taps - 1))))
    total = sum(kernel)
    kernel = [k / total for k in kernel]
    padded = [0.0] * middle + list(values) + [0.0] * middle
    return [sum(kernel[k] * padded[i + k] for k in range(taps)) for i in range(len(values))]


def _ulaw(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    magnitude = min(abs(sample), 32635) + 132
    exponent = max(0, magnitude.bit_length() - 8)
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def _ulaw_to_linear(byte: int) -> int:
    byte = ~byte & 0xFF
    sign, exponent, mantissa = byte & 0x80, (byte >> 4) & 0x07, byte & 0x0F
    sample = ((mantissa << 3) + 132) << exponent
    return -(sample - 132) if sign else sample - 132


def telephone_line(pcm24: bytes) -> bytes:
    """What a caller's voice becomes on a G.711 phone line: band-limited, 8 kHz, mu-law.
    Returned at 8 kHz (16-bit) for the transcription API, as Asterisk would hear it."""
    filtered = _lowpass(samples(pcm24))[::3]
    line = array("h", (_ulaw_to_linear(_ulaw(int(max(-32768, min(32767, v))))) for v in filtered))
    return to_bytes(line)
