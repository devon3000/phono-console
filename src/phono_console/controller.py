from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from .config import Config
from .detector import ActivityDetector
from .interfaces import AudioRouter, EventSink, LevelMonitor, MusicAssistant, StatusSink
from .policy import Inputs, Route, choose_route

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Status:
    route: Route
    phono_active: bool
    ma_playing: bool
    whole_house_requested: bool
    level_dbfs: float


class Controller:
    def __init__(
        self,
        config: Config,
        level_monitor: LevelMonitor,
        music_assistant: MusicAssistant,
        audio_router: AudioRouter,
        event_sink: EventSink,
        status_sink: StatusSink | None = None,
    ) -> None:
        self.config = config
        self.level_monitor = level_monitor
        self.music_assistant = music_assistant
        self.audio_router = audio_router
        self.event_sink = event_sink
        self.status_sink = status_sink
        self.detector = ActivityDetector(
            threshold_dbfs=config.detection.phono_threshold_dbfs,
            attack_seconds=config.detection.attack_ms / 1000,
            release_seconds=config.detection.release_ms / 1000,
            hysteresis_db=config.detection.hysteresis_db,
        )
        self.route: Route | None = None

    async def tick(self, now: float | None = None) -> Status:
        level, ma_playing, whole_house = await asyncio.gather(
            self.level_monitor.level_dbfs(),
            self.music_assistant.console_is_playing(),
            self.music_assistant.whole_house_is_requested(),
        )
        phono_active = self.detector.update(
            level, time.monotonic() if now is None else now
        )
        inputs = Inputs(phono_active, ma_playing, whole_house)
        route = choose_route(inputs)

        if route != self.route:
            LOGGER.info(
                "route transition %s -> %s",
                self.route.value if self.route else "startup",
                route.value,
            )
            await self.audio_router.apply(route)
            await self.event_sink.emit(
                "route_changed",
                {
                    "previous": self.route.value if self.route else None,
                    "current": route.value,
                    "phono_active": phono_active,
                    "ma_playing": ma_playing,
                    "whole_house_requested": whole_house,
                    "level_dbfs": level,
                },
            )
            self.route = route

        status = Status(route, phono_active, ma_playing, whole_house, level)
        if self.status_sink is not None:
            await self.status_sink.set_status(status)
        return status

    async def run(self, stop: asyncio.Event) -> None:
        interval = self.config.runtime.poll_interval_ms / 1000
        try:
            while not stop.is_set():
                started = time.monotonic()
                await self.tick(started)
                remaining = interval - (time.monotonic() - started)
                if remaining > 0:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=remaining)
                    except TimeoutError:
                        pass
        finally:
            await asyncio.gather(
                self.audio_router.close(),
                self.level_monitor.close(),
                self.music_assistant.close(),
            )
