import struct

from phono_console.levels import LevelSession, analyze_s16le_stereo
from phono_console.terminal_meter import render, summary


def test_render_and_summary_include_stereo_maxima_and_clip_latch() -> None:
    pcm = struct.pack("<4h", 32767, 4096, -1000, -4096)
    level = analyze_s16le_stereo(pcm)
    session = LevelSession()
    session.update(level)
    display = render(level, session)
    assert "UFO202 INPUT" in display
    assert "MAX L:   0.0 dBFS" in display
    assert "CLIP:  L YES" in display
    result = summary(session)
    assert "calibration summary" in result
    assert "L YES" in result
