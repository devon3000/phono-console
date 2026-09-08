from __future__ import annotations

import asyncio
import time
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
        self.started_at = datetime.now(UTC)
        self._started_monotonic = time.monotonic()
        self._last_status_monotonic: float | None = None
        self.status: Status | None = None
        self.whole_house_requested = False
        self.sendspin_source: dict[str, object] = {}
        self.music_assistant: dict[str, object] = {}
        self.system: dict[str, object] = {}
        self.input_levels: dict[str, object] | None = None
        self.components: dict[str, dict[str, object]] = {}
        self.events: deque[RecordedEvent] = deque(maxlen=max_events)
        self.changed = asyncio.Condition()

    async def set_status(self, status: Status) -> None:
        async with self.changed:
            self.status = status
            self._last_status_monotonic = time.monotonic()
            self.changed.notify_all()

    async def set_component(
        self,
        name: str,
        status: str,
        message: str = "",
        **details: object,
    ) -> None:
        if status not in {"ok", "degraded", "failed"}:
            raise ValueError(f"invalid component status: {status}")
        async with self.changed:
            previous = self.components.get(name)
            payload: dict[str, object] = {
                "status": status,
                "message": message,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            payload.update(details)
            # Avoid making a healthy component look continuously fresh merely
            # because the dashboard is polled; preserve the transition time.
            if previous is not None and all(
                previous.get(key) == value
                for key, value in payload.items()
                if key != "updated_at"
            ):
                payload["updated_at"] = previous["updated_at"]
            self.components[name] = payload
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

    async def clear_input_levels(self) -> None:
        async with self.changed:
            self.input_levels = None
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
        health = self.health_snapshot()
        return {
            "status": status,
            "whole_house_requested": self.whole_house_requested,
            "sendspin_source": dict(self.sendspin_source),
            "music_assistant": dict(self.music_assistant),
            "system": dict(self.system),
            "input_levels": self.input_levels,
            "health": health,
            "components": {
                name: dict(component)
                for name, component in self.components.items()
            },
            "events": [asdict(event) for event in self.events],
        }

    def health_snapshot(self, *, stale_after_seconds: float = 3.0) -> dict[str, object]:
        now = time.monotonic()
        status_age = (
            None
            if self._last_status_monotonic is None
            else max(0.0, now - self._last_status_monotonic)
        )
        controller_fresh = status_age is not None and status_age <= stale_after_seconds
        failed = sorted(
            name
            for name, component in self.components.items()
            if component.get("status") == "failed"
        )
        degraded = sorted(
            name
            for name, component in self.components.items()
            if component.get("status") == "degraded"
        )
        operational = controller_fresh and not failed
        return {
            # The HTTP/dashboard process is live even when audio hardware is
            # absent. "operational" separately reports audio readiness.
            "live": True,
            "operational": operational,
            "status": "ok" if operational and not degraded else "degraded",
            "uptime_seconds": round(max(0.0, now - self._started_monotonic), 1),
            "started_at": self.started_at.isoformat(),
            "controller_fresh": controller_fresh,
            "status_age_seconds": None if status_age is None else round(status_age, 2),
            "failed_components": failed,
            "degraded_components": degraded,
        }
