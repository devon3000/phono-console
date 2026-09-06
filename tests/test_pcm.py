import math
import struct

import pytest

from phono_console.pcm import SILENCE_DBFS, rms_dbfs_s16le


def test_silence_has_finite_floor() -> None:
    assert rms_dbfs_s16le(b"\0" * 16) == SILENCE_DBFS


def test_full_scale_constant_is_near_zero_dbfs() -> None:
    pcm = struct.pack("<8h", *([32767] * 8))
    assert math.isclose(rms_dbfs_s16le(pcm), 0.0, abs_tol=0.001)


def test_rejects_partial_sample() -> None:
    with pytest.raises(ValueError):
        rms_dbfs_s16le(b"\0")
