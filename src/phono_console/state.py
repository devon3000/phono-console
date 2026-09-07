from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from .controller import Status
from .levels import LevelSession, StereoLevel


@dataclass(frozen=True)
class RecordedEvent:
    timestamp: str
    event: str
    details: dict[str, object]


class StateStore:
    """Thread-safe-enough asyncio state shared by the controller and HTTP API."""

    def __init__(self, max_events: int = 100) -> None:
        self.status: Status | None = None
        self.whole_house_requested = False
        self.sendspin_source: dict[str, object] = {}
        self.music_assistant: dict[str, object] = {}
        self.system: dict[str, object] = {}
        self.input_levels: dict[str, object] | None = None
        self.events: deque[RecordedEvent] = deque(maxlen=max_events)
        self.changed = asyncio.Condition()

    async def set_status(self, status: Status) -> None:
        async with self.changed:
            self.status = status
            self.changed.notify_all()

    async def set_source_state(self, source: dict[str, object]) -> None:
        async with self.changed:
            self.sendspin_source = dict(source)
            self.changed.notify_all()

    async def set_music_assistant_state(self, state: dict[str, object]) -> None:
        async with self.changed:
            self.music_assistant = dict(state)
            self.changed.notify_all()

    async def set_system_info(self, info: dict[str, object]) -> None:
        async with self.changed:
            self.system = dict(info)
            self.changed.notify_all()

    async def set_input_levels(
        self, level: StereoLevel, session: LevelSession
    ) -> None:
        async with self.changed:
            self.input_levels = {
                "left": {
                    "peak_dbfs": level.left.peak_dbfs,
                    "rms_dbfs": level.left.rms_dbfs,
                    "max_peak_dbfs": session.max_left_dbfs,
                    "clipped": session.left_clipped,
                },
                "right": {
                    "peak_dbfs": level.right.peak_dbfs,
                    "rms_dbfs": level.right.rms_dbfs,
                    "max_peak_dbfs": session.max_right_dbfs,
                    "clipped": session.right_clipped,
                },
            }
            self.changed.notify_all()

    async def reset_input_level_history(self) -> None:
        async with self.changed:
            if self.input_levels is not None:
                for channel in ("left", "right"):
                    values = self.input_levels[channel]
                    assert isinstance(values, dict)
                    values["max_peak_dbfs"] = -120.0
                    values["clipped"] = False
            self.changed.notify_all()

    async def request_whole_house(self, requested: bool) -> None:
        async with self.changed:
            self.whole_house_requested = requested
            self.changed.notify_all()

    async def emit(self, event: str, details: dict[str, object]) -> None:
        async with self.changed:
            self.events.append(
                RecordedEvent(datetime.now(UTC).isoformat(), event, dict(details))
            )
            self.changed.notify_all()

    def snapshot(self) -> dict[str, object]:
        status = None
        if self.status is not None:
            status = asdict(self.status)
            status["route"] = self.status.route.value
        return {
            "status": status,
            "whole_house_requested": self.whole_house_requested,
            "sendspin_source": dict(self.sendspin_source),
            "music_assistant": dict(self.music_assistant),
            "system": dict(self.system),
            "input_levels": self.input_levels,
            "events": [asdict(event) for event in self.events],
        }
