from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Mode(StrEnum):
    LOCAL_VINYL = "local_vinyl"
    BROADCAST_VINYL = "broadcast_vinyl"
    MUSIC_ASSISTANT = "music_assistant"


@dataclass(frozen=True)
class DesiredState:
    amplifier_input: str
    capture_enabled: bool
    console_stream_enabled: bool
    house_stream_enabled: bool


MODE_STATES = {
    Mode.LOCAL_VINYL: DesiredState(
        amplifier_input="phono",
        capture_enabled=False,
        console_stream_enabled=False,
        house_stream_enabled=False,
    ),
    Mode.BROADCAST_VINYL: DesiredState(
        amplifier_input="pi",
        capture_enabled=True,
        console_stream_enabled=True,
        house_stream_enabled=True,
    ),
    Mode.MUSIC_ASSISTANT: DesiredState(
        amplifier_input="pi",
        capture_enabled=False,
        console_stream_enabled=True,
        house_stream_enabled=False,
    ),
}


def desired_state(mode: Mode) -> DesiredState:
    return MODE_STATES[mode]

