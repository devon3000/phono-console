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
        self._last_route_error: str | None = None

    async def tick(self, now: float | None = None) -> Status:
        timestamp = time.monotonic() if now is None else now
        capture_ok = True
        try:
            level = await asyncio.wait_for(
                self.level_monitor.level_dbfs(), timeout=2.0
            )
        except Exception as exc:
            capture_ok = False
            level = -120.0
            # A dead capture must silence the local route immediately; the
            # normal release delay only applies to valid quiet PCM.
            self.detector.reset_inactive()
            await self._set_component_from_monitor("capture", "failed", str(exc))
        else:
            await self._set_component_from_monitor(
                "capture", "ok", "capture is producing PCM"
            )

        try:
            ma_playing = await asyncio.wait_for(
                self.music_assistant.console_is_playing(), timeout=2.0
            )
        except Exception as exc:
            # Reserve the output if telemetry fails. This prevents local audio
            # being mixed into a network stream whose state became unknown.
            ma_playing = True
            await self._set_component("music_assistant", "degraded", str(exc))
        whole_house = await self.music_assistant.whole_house_is_requested()
        phono_active = capture_ok and self.detector.update(level, timestamp)
        inputs = Inputs(phono_active, ma_playing, whole_house)
        desired_route = choose_route(inputs)
        route = desired_route

        if desired_route != self.route:
            LOGGER.info(
                "route transition %s -> %s",
                self.route.value if self.route else "startup",
                desired_route.value,
            )
        try:
            # Reconcile every tick so a child process that dies while the
            # desired route is unchanged is supervised and restarted.
            reconciler = getattr(self.audio_router, "reconcile", None)
            if desired_route == self.route and reconciler is not None:
                await reconciler(desired_route)
            elif desired_route != self.route:
                await self.audio_router.apply(desired_route)
            await self._set_router_component(desired_route)
            if self._last_route_error is not None:
                await self.event_sink.emit(
                    "route_apply_recovered", {"route": desired_route.value}
                )
                self._last_route_error = None
        except Exception as exc:
            await self._set_component("local_output", "failed", str(exc))
            error = str(exc)
            if error != self._last_route_error:
                LOGGER.error("audio route %s failed: %s", desired_route.value, exc)
                await self.event_sink.emit(
                    "route_apply_failed",
                    {"route": desired_route.value, "error": error},
                )
                self._last_route_error = error
            route = Route.IDLE
            try:
                await self.audio_router.apply(Route.IDLE)
            except Exception:
                pass

        if route != self.route:
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
            latest = getattr(self.level_monitor, "latest", None)
            session = getattr(self.level_monitor, "session", None)
            set_levels = getattr(self.status_sink, "set_input_levels", None)
            if (
                capture_ok
                and latest is not None
                and session is not None
                and set_levels is not None
            ):
                await set_levels(latest, session)
            elif not capture_ok:
                clear_levels = getattr(
                    self.status_sink, "clear_input_levels", None
                )
                if clear_levels is not None:
                    await clear_levels()
        return status

    async def _set_component(
        self, name: str, status: str, message: str, **details: object
    ) -> None:
        setter = getattr(self.status_sink, "set_component", None)
        if setter is not None:
            await setter(name, status, message, **details)

    async def _set_component_from_monitor(
        self, name: str, fallback_status: str, fallback_message: str
    ) -> None:
        health = getattr(self.level_monitor, "health", None)
        if isinstance(health, dict):
            payload = dict(health)
            status = str(payload.pop("status", fallback_status))
            message = str(payload.pop("message", fallback_message))
            await self._set_component(name, status, message, **payload)
        else:
            await self._set_component(name, fallback_status, fallback_message)

    async def _set_router_component(self, route: Route) -> None:
        process = getattr(self.audio_router, "local_loopback", None)
        health = getattr(process, "health", None)
        if route is Route.LOCAL_PHONO and isinstance(health, dict):
            payload = dict(health)
            status = str(payload.pop("status", "failed"))
            message = str(payload.pop("message", "unknown"))
            await self._set_component("local_output", status, message, **payload)
        else:
            await self._set_component(
                "local_output", "ok", "standby", route=route.value
            )

    async def run(self, stop: asyncio.Event) -> None:
        interval = self.config.runtime.poll_interval_ms / 1000
        try:
            while not stop.is_set():
                started = time.monotonic()
                try:
                    await self.tick(started)
                except Exception as exc:
                    LOGGER.warning("controller tick failed: %s", exc)
                    await self.event_sink.emit(
                        "controller_tick_failed", {"error": str(exc)}
                    )
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
