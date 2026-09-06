from __future__ import annotations

import asyncio
import sys

from .alsa import ArecordLevelMonitor
from .levels import FLOOR_DBFS, LevelSession, StereoLevel


def _bar(dbfs: float, width: int = 26, floor: float = -60.0) -> str:
    fraction = max(0.0, min(1.0, (dbfs - floor) / -floor))
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


def _value(dbfs: float) -> str:
    return "-inf" if dbfs <= FLOOR_DBFS else f"{dbfs:5.1f}"


def render(
    level: StereoLevel,
    session: LevelSession,
    *,
    sample_rate: int = 48_000,
) -> str:
    rate = f"{sample_rate / 1000:g} kHz"
    return "\n".join(
        (
            f"UFO202 INPUT — {rate} / 16-bit / stereo",
            f"L  {_bar(level.left.peak_dbfs)}  {_value(level.left.peak_dbfs)} dBFS"
            f"  RMS {_value(level.left.rms_dbfs)}",
            f"R  {_bar(level.right.peak_dbfs)}  {_value(level.right.peak_dbfs)} dBFS"
            f"  RMS {_value(level.right.rms_dbfs)}",
            f"MAX L: {_value(session.max_left_dbfs)} dBFS",
            f"MAX R: {_value(session.max_right_dbfs)} dBFS",
            "CLIP:  "
            f"L {'YES' if session.left_clipped else 'no '}   "
            f"R {'YES' if session.right_clipped else 'no '}",
            "Ctrl-C to finish",
        )
    )


def summary(session: LevelSession) -> str:
    return "\n".join(
        (
            "UFO202 calibration summary",
            f"MAX L: {_value(session.max_left_dbfs)} dBFS",
            f"MAX R: {_value(session.max_right_dbfs)} dBFS",
            "CLIPPED: "
            f"L {'YES' if session.left_clipped else 'no'}  "
            f"R {'YES' if session.right_clipped else 'no'}",
        )
    )


async def run_terminal_meter(capture: ArecordLevelMonitor) -> LevelSession:
    if capture.channels != 2:
        raise ValueError("level calibration requires a stereo capture configuration")
    session = capture.session
    first = True
    try:
        while True:
            await capture.read_pcm()
            assert capture.latest is not None
            level = capture.latest
            if not first:
                sys.stdout.write("\x1b[7A")
            sys.stdout.write(
                render(level, session, sample_rate=capture.sample_rate) + "\n"
            )
            sys.stdout.flush()
            first = False
            await asyncio.sleep(0)
    finally:
        await capture.close()
        if not first:
            sys.stdout.write("\n" + summary(session) + "\n")
            sys.stdout.flush()
