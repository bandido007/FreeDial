"""When the caller starts and stops talking (voice activity detection), from 20 ms frames.

Energy based, with a noise floor that follows the line: a frame is speech when it is clearly
louder than the floor. Speech that lasts ``start_ms`` starts a turn (and lets the caller
interrupt the AI); ``end_ms`` of quiet ends it. Short clicks never become turns, and a turn
that runs on past ``max_ms`` is cut so the caller is not ignored forever.
"""

from collections import deque
from dataclasses import dataclass

from aiworker.core.audio import FRAME_MS, rms


@dataclass(frozen=True)
class SpeechStarted:
    """The caller started talking (the AI stops speaking: barge-in)."""


@dataclass(frozen=True)
class TurnEnded:
    pcm: bytes  # the caller's words, with a little audio from before they started


class TurnDetector:
    def __init__(
        self,
        *,
        start_ms: int = 120,
        end_ms: int = 700,
        min_ms: int = 300,
        max_ms: int = 20000,
        preroll_ms: int = 300,
        floor: float = 150.0,  # quietest line we assume (16-bit RMS)
        ratio: float = 3.0,  # speech is this many times louder than the floor
    ):
        self.start_frames = max(1, start_ms // FRAME_MS)
        self.end_frames = max(1, end_ms // FRAME_MS)
        self.min_frames = max(1, min_ms // FRAME_MS)
        self.max_frames = max_ms // FRAME_MS
        self.ratio = ratio
        self.minimum_floor = floor
        self.floor = floor
        self.preroll: deque[bytes] = deque(maxlen=max(1, preroll_ms // FRAME_MS))
        self.turn: list[bytes] = []
        self.loud = 0  # loud frames in a row
        self.quiet = 0  # quiet frames in a row, inside a turn
        self.voiced = 0  # loud frames in this turn
        self.talking = False

    def feed(self, frame: bytes) -> list[SpeechStarted | TurnEnded]:
        level = rms(frame)
        loud = level > self.floor * self.ratio
        # The floor follows the line's noise: down at once, up slowly (even through loud frames, so a
        # steady hum is learnt in a few seconds while words, louder still, stay above it).
        if level > self.floor:
            self.floor = min(level, self.floor * (1.005 if loud else 1.02))
        else:
            self.floor = max(self.minimum_floor, level)
        if not self.talking:
            self.preroll.append(frame)
            self.loud = self.loud + 1 if loud else 0
            if self.loud < self.start_frames:
                return []
            self.talking, self.turn = True, list(self.preroll)
            self.preroll.clear()
            self.voiced, self.quiet = self.loud, 0
            return [SpeechStarted()]
        self.turn.append(frame)
        if loud:
            self.voiced, self.quiet = self.voiced + 1, 0
        else:
            self.quiet += 1
        if self.quiet >= self.end_frames or len(self.turn) >= self.max_frames:
            return self._end()
        return []

    def _end(self) -> list[SpeechStarted | TurnEnded]:
        turn, voiced = self.turn, self.voiced
        self.talking, self.turn, self.loud, self.quiet, self.voiced = False, [], 0, 0, 0
        if voiced < self.min_frames:
            return []  # a cough or a click
        return [TurnEnded(b"".join(turn))]
