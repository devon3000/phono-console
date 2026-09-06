import math
import struct

import pytest

from phono_console.levels import LevelSession, analyze_s16le_stereo


def frames(left: list[int], right: list[int]) -> bytes:
    values = [sample for pair in zip(left, right, strict=True) for sample in pair]
    return struct.pack(f"<{len(values)}h", *values)


def test_independent_stereo_peak_and_rms() -> None:
    level = analyze_s16le_stereo(frames([16384, -16384], [8192, -8192]))
    assert math.isclose(level.left.peak_dbfs, -6.02, abs_tol=0.02)
    assert math.isclose(level.right.peak_dbfs, -12.04, abs_tol=0.02)
    assert math.isclose(level.left.rms_dbfs, -6.02, abs_tol=0.02)


def test_clip_and_maxima_latch_until_reset() -> None:
    session = LevelSession()
    session.update(analyze_s16le_stereo(frames([32767], [1000])))
    session.update(analyze_s16le_stereo(frames([100], [-32768])))
    session.update(analyze_s16le_stereo(frames([10], [10])))
    assert session.left_clipped and session.right_clipped
    assert session.max_left_dbfs == 0
    assert session.max_right_dbfs == 0
    session.reset()
    assert not session.left_clipped and not session.right_clipped


def test_rejects_incomplete_stereo_frame() -> None:
    with pytest.raises(ValueError):
        analyze_s16le_stereo(b"\0\0")
