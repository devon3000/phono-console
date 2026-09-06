from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from .controller import Status


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
        self.events: deque[RecordedEvent] = deque(maxlen=max_events)
        self.changed = asyncio.Condition()

    async def set_status(self, status: Status) -> None:
        async with self.changed:
            self.status = status
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
            "events": [asdict(event) for event in self.events],
        }

