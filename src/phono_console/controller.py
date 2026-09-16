from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .config import Config
from .detector import ActivityDetector
from .interfaces import AudioRouter, EventSink, LevelMonitor, MusicAssistant, StatusSink
from .policy import (
    DistributionPath,
    Inputs,
    Route,
    Source,
    choose_route,
    route_distribution,
    route_source,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Status:
    route: Route
    phono_active: bool
    ma_playing: bool
    whole_house_requested: bool
    level_dbfs: float
    bluetooth_active: bool = False
    bluetooth_level_dbfs: float = -120.0
    source: Source = Source.NONE
    distribution: DistributionPath = DistributionPath.NONE


class Controller:
    def __init__(
        self,
        config: Config,
        level_monitor: LevelMonitor,
        music_assistant: MusicAssistant,
        audio_router: AudioRouter,
        event_sink: EventSink,
        status_sink: StatusSink | None = None,
        bluetooth_monitor: LevelMonitor | None = None,
        distribution_available: Callable[[], bool | Awaitable[bool]] | None = None,
        distribution_capable: Callable[
            [Source], bool | Awaitable[bool]
        ] | None = None,
        prepare_distribution: Callable[[Source], Awaitable[bool]] | None = None,
        release_distribution: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.config = config
        self.level_monitor = level_monitor
        self.music_assistant = music_assistant
        self.audio_router = audio_router
        self.event_sink = event_sink
        self.status_sink = status_sink
        self.bluetooth_monitor = bluetooth_monitor
        self.distribution_available = distribution_available
        self.distribution_capable = distribution_capable
        self.prepare_distribution = prepare_distribution
        self.release_distribution = release_distribution
        self.detector = ActivityDetector(
            threshold_dbfs=config.detection.phono_threshold_dbfs,
            attack_seconds=config.detection.attack_ms / 1000,
            release_seconds=config.detection.release_ms / 1000,
            hysteresis_db=config.detection.hysteresis_db,
        )
        self.bluetooth_detector = ActivityDetector(
            threshold_dbfs=config.bluetooth.threshold_dbfs,
            attack_seconds=config.bluetooth.attack_ms / 1000,
            release_seconds=config.bluetooth.release_ms / 1000,
            hysteresis_db=3.0,
        )
        self.route: Route | None = None
        self._last_route_error: str | None = None
        self._distribution_healthy_since: float | None = None
        self._distribution_pending_source: Source | None = None
        self._distribution_pending_since: float | None = None

    async def _distribution_ready(self, now: float) -> bool:
        if self.distribution_available is None:
            return False
        result = self.distribution_available()
        healthy = await result if hasattr(result, "__await__") else bool(result)
        if not healthy:
            self._distribution_healthy_since = None
            return False
        # Runtime availability already requires an explicit Sendspin
        # source.start request. Waiting a second recovery interval here leaves
        # the direct local route active while MA is returning that same source,
        # causing the handoff to oscillate for the duration of the hold.
        self._distribution_healthy_since = now
        return True

    async def _distribution_is_capable(self, source: Source) -> bool:
        if self.distribution_capable is None:
            return False
        result = self.distribution_capable(source)
        return await result if hasattr(result, "__await__") else bool(result)

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
        bluetooth_level = -120.0
        bluetooth_active = False
        bluetooth_capture_ok = self.bluetooth_monitor is not None
        if self.bluetooth_monitor is not None:
            try:
                bluetooth_level = await asyncio.wait_for(
                    self.bluetooth_monitor.level_dbfs(), timeout=2.0
                )
            except Exception as exc:
                bluetooth_capture_ok = False
                self.bluetooth_detector.reset_inactive()
                await self._set_component("bluetooth_audio", "degraded", str(exc))
            else:
                bluetooth_active = self.bluetooth_detector.update(
                    bluetooth_level, timestamp
                )
                await self._set_component(
                    "bluetooth_audio",
                    "ok",
                    "streaming" if bluetooth_active else "connected or idle",
                    level_dbfs=bluetooth_level,
                )
        available = await self._distribution_ready(timestamp)
        selected_source = (
            Source.PHONO
            if phono_active
            else (Source.BLUETOOTH if bluetooth_active else Source.NONE)
        )
        distribution_pending = False
        capable = await self._distribution_is_capable(selected_source)
        if selected_source is not Source.NONE and not available and capable:
            if self._distribution_pending_source is not selected_source:
                self._distribution_pending_source = selected_source
                self._distribution_pending_since = timestamp
                try:
                    if self.prepare_distribution is not None:
                        await self.prepare_distribution(selected_source)
                except Exception as exc:
                    self._distribution_pending_since = timestamp - (
                        self.config.routing.distribution_start_timeout_ms / 1000
                    )
                    await self._set_component(
                        "distribution", "degraded", str(exc)
                    )
            if self._distribution_pending_since is not None:
                elapsed_ms = (
                    timestamp - self._distribution_pending_since
                ) * 1000
                distribution_pending = (
                    elapsed_ms
                    < self.config.routing.distribution_start_timeout_ms
                )
        elif available or selected_source is Source.NONE:
            self._distribution_pending_source = None
            self._distribution_pending_since = None

        inputs = Inputs(phono_active, bluetooth_active, ma_playing, available)
        desired_route = Route.IDLE if distribution_pending else choose_route(inputs)
        # Once Music Assistant has requested a source stream, its source.stop
        # command is the authority for ending that distributed session.  The
        # local level detector can briefly read silence while ALSA consumers
        # start or buffers settle; treating that gap as source-off creates a
        # destructive loop (stop MA, fall back locally, request MA again).
        # Keep the active distributed path latched, while still allowing the
        # higher-priority phono input to preempt Bluetooth and Bluetooth to
        # take over after phono has genuinely released.
        if available:
            if (
                self.route is Route.DISTRIBUTED_BLUETOOTH
                and bluetooth_active
                and not phono_active
            ):
                desired_route = Route.DISTRIBUTED_BLUETOOTH
            elif self.route is Route.DISTRIBUTED_PHONO and phono_active:
                desired_route = Route.DISTRIBUTED_PHONO
        was_distributed = self.route in {
            Route.DISTRIBUTED_PHONO,
            Route.DISTRIBUTED_BLUETOOTH,
        }
        will_be_distributed = desired_route in {
            Route.DISTRIBUTED_PHONO,
            Route.DISTRIBUTED_BLUETOOTH,
        }
        if was_distributed and not will_be_distributed:
            if self.release_distribution is not None:
                try:
                    await self.release_distribution()
                except Exception as exc:
                    await self._set_component("distribution", "degraded", str(exc))
            # Telemetry may still report the live input that was just stopped.
            if desired_route is Route.MA_PLAYBACK:
                desired_route = Route.IDLE
        if (
            desired_route in {Route.DISTRIBUTED_PHONO, Route.DISTRIBUTED_BLUETOOTH}
            and desired_route != self.route
            and self.prepare_distribution is not None
        ):
            # Release the direct hardware endpoint before asking MA/Sendspin
            # to start the synchronized return path.
            await self.audio_router.apply(Route.IDLE)
            try:
                prepared = await self.prepare_distribution(route_source(desired_route))
            except Exception as exc:
                prepared = False
                await self._set_component("distribution", "degraded", str(exc))
            if not prepared:
                self._distribution_healthy_since = None
                desired_route = choose_route(
                    Inputs(phono_active, bluetooth_active, ma_playing, False)
                )
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
                    "bluetooth_active": bluetooth_active,
                    "source": route_source(route).value,
                    "distribution": route_distribution(route).value,
                    "level_dbfs": level,
                },
            )
            self.route = route

        status = Status(
            route,
            phono_active,
            ma_playing,
            whole_house,
            level,
            bluetooth_active,
            bluetooth_level,
            route_source(route),
            route_distribution(route),
        )
        if self.status_sink is not None:
            await self.status_sink.set_status(status)
            selected_monitor = (
                self.bluetooth_monitor
                if route_source(route) is Source.BLUETOOTH
                else self.level_monitor
            )
            selected_capture_ok = (
                bluetooth_capture_ok
                if selected_monitor is self.bluetooth_monitor
                else capture_ok
            )
            latest = getattr(selected_monitor, "latest", None)
            session = getattr(selected_monitor, "session", None)
            set_levels = getattr(self.status_sink, "set_input_levels", None)
            if (
                selected_capture_ok
                and latest is not None
                and session is not None
                and set_levels is not None
            ):
                await set_levels(latest, session)
            elif not selected_capture_ok:
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
        process = (
            getattr(self.audio_router, "bluetooth_loopback", None)
            if route is Route.LOCAL_BLUETOOTH
            else getattr(self.audio_router, "local_loopback", None)
        )
        health = getattr(process, "health", None)
        if route in {Route.LOCAL_PHONO, Route.LOCAL_BLUETOOTH} and isinstance(
            health, dict
        ):
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
                *(
                    [self.bluetooth_monitor.close()]
                    if self.bluetooth_monitor is not None
                    else []
                ),
            )
