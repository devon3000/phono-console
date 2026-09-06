from __future__ import annotations

import math
from array import array


S16_MAX = 32768.0
SILENCE_DBFS = -120.0


def rms_dbfs_s16le(pcm: bytes) -> float:
    """Return RMS dBFS for signed 16-bit little-endian interleaved PCM."""
    if not pcm:
        return SILENCE_DBFS
    if len(pcm) % 2:
        raise ValueError("s16le PCM must contain complete 16-bit samples")
    samples = array("h")
    samples.frombytes(pcm)
    if samples.itemsize != 2:
        raise RuntimeError("platform does not provide 16-bit array('h')")
    # Raspberry Pi is little-endian; retain correctness on other development hosts.
    import sys

    if sys.byteorder != "little":
        samples.byteswap()
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    if mean_square == 0:
        return SILENCE_DBFS
    return 20 * math.log10(math.sqrt(mean_square) / S16_MAX)

