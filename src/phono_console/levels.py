from __future__ import annotations

import math
from array import array
from dataclasses import dataclass

FLOOR_DBFS = -120.0
FULL_SCALE = 32768.0


def _dbfs(amplitude: float) -> float:
    if amplitude <= 0:
        return FLOOR_DBFS
    if amplitude >= 32767:
        return 0.0
    return min(0.0, 20 * math.log10(amplitude / FULL_SCALE))


@dataclass(frozen=True)
class ChannelLevel:
    peak_dbfs: float
    rms_dbfs: float
    clipped: bool


@dataclass(frozen=True)
class StereoLevel:
    left: ChannelLevel
    right: ChannelLevel


def analyze_s16le_stereo(pcm: bytes) -> StereoLevel:
    """Measure stereo PCM without modifying or copying it into an audio path."""
    if not pcm or len(pcm) % 4:
        raise ValueError("stereo s16le PCM must contain complete four-byte frames")
    samples = array("h")
    samples.frombytes(pcm)
    import sys

    if sys.byteorder != "little":
        samples.byteswap()

    def channel(offset: int) -> ChannelLevel:
        values = samples[offset::2]
        peak = max(abs(value) for value in values)
        rms = math.sqrt(sum(value * value for value in values) / len(values))
        clipped = any(value in (-32768, 32767) for value in values)
        return ChannelLevel(_dbfs(peak), _dbfs(rms), clipped)

    return StereoLevel(channel(0), channel(1))


@dataclass
class LevelSession:
    max_left_dbfs: float = FLOOR_DBFS
    max_right_dbfs: float = FLOOR_DBFS
    left_clipped: bool = False
    right_clipped: bool = False

    def update(self, level: StereoLevel) -> None:
        self.max_left_dbfs = max(self.max_left_dbfs, level.left.peak_dbfs)
        self.max_right_dbfs = max(self.max_right_dbfs, level.right.peak_dbfs)
        self.left_clipped |= level.left.clipped
        self.right_clipped |= level.right.clipped

    def reset(self) -> None:
        self.max_left_dbfs = FLOOR_DBFS
        self.max_right_dbfs = FLOOR_DBFS
        self.left_clipped = False
        self.right_clipped = False
