from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .config import ControlsConfig
from .interfaces import EventSink
from .policy import Route
from .state import StateStore


TurnAction = Callable[[int], Awaitable[None]]
PressAction = Callable[[], Awaitable[None]]
TurnCallback = Callable[[int], None]
PressCallback = Callable[[], None]


class VolumeTarget(Enum):
    LOCAL = "local"
    DOWNSTAIRS = "downstairs"
    NONE = "none"


class PressCommand(Enum):
    PHONO_DOWNSTAIRS = "phono_downstairs"
    PHONO_LOCAL = "phono_local"
    STOP_BLUETOOTH = "stop_bluetooth"
    STOP_MA = "stop_ma"
    NONE = "none"


def volume_target_for_route(route: Route) -> VolumeTarget:
    if route in {Route.LOCAL_PHONO, Route.LOCAL_BLUETOOTH}:
        return VolumeTarget.LOCAL
    if route in {
        Route.MA_PLAYBACK,
        Route.DISTRIBUTED_PHONO,
        Route.DISTRIBUTED_BLUETOOTH,
    }:
        return VolumeTarget.DOWNSTAIRS
    return VolumeTarget.NONE


def press_command_for_route(route: Route) -> PressCommand:
    return {
        Route.LOCAL_PHONO: PressCommand.PHONO_DOWNSTAIRS,
        Route.DISTRIBUTED_PHONO: PressCommand.PHONO_LOCAL,
        Route.LOCAL_BLUETOOTH: PressCommand.STOP_BLUETOOTH,
        Route.DISTRIBUTED_BLUETOOTH: PressCommand.STOP_BLUETOOTH,
        Route.MA_PLAYBACK: PressCommand.STOP_MA,
    }.get(route, PressCommand.NONE)


class EncoderInput(Protocol):
    def open(self, on_turn: TurnCallback, on_press: PressCallback) -> None: ...

    def close(self) -> None: ...


class GpioZeroEncoderInput:
    """PEC11H GPIO adapter, imported lazily so tests need no Pi hardware."""

    def __init__(self, config: ControlsConfig) -> None:
        self.config = config
        self._encoder: object | None = None
        self._button: object | None = None

    def open(self, on_turn: TurnCallback, on_press: PressCallback) -> None:
        from gpiozero import Button, RotaryEncoder

        encoder = RotaryEncoder(
            self.config.encoder_a_gpio,
            self.config.encoder_b_gpio,
            bounce_time=self.config.encoder_bounce_ms / 1000,
            max_steps=0,
        )
        direction = -1 if self.config.reverse else 1
        encoder.when_rotated_clockwise = lambda: on_turn(direction)
        encoder.when_rotated_counter_clockwise = lambda: on_turn(-direction)
        button = Button(
            self.config.encoder_button_gpio,
            pull_up=True,
            bounce_time=self.config.button_bounce_ms / 1000,
        )
        button.when_pressed = on_press
        self._encoder = encoder
        self._button = button

    def close(self) -> None:
        for device in (self._button, self._encoder):
            if device is not None:
                device.close()
        self._button = None
        self._encoder = None


@dataclass(frozen=True)
class _ControlEvent:
    kind: str
    delta: int = 0


class HardwareControls:
    """Bridge threaded GPIO callbacks into serialized asyncio control actions."""

    def __init__(
        self,
        config: ControlsConfig,
        events: EventSink,
        state: StateStore,
        turn_action: TurnAction,
        press_action: PressAction,
        *,
        input_device: EncoderInput | None = None,
        coalesce_seconds: float = 0.04,
    ) -> None:
        self.config = config
        self.events = events
        self.state = state
        self.turn_action = turn_action
        self.press_action = press_action
        self.input = input_device or GpioZeroEncoderInput(config)
        self.coalesce_seconds = coalesce_seconds
        self._queue: asyncio.Queue[_ControlEvent] = asyncio.Queue(maxsize=128)
        self._pending: deque[_ControlEvent] = deque()
        self._loop: asyncio.AbstractEventLoop | None = None

    def _enqueue(self, event: _ControlEvent) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(event)

    def _from_gpio(self, event: _ControlEvent) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._enqueue, event)

    async def _next_event(self, stop: asyncio.Event) -> _ControlEvent | None:
        if self._pending:
            return self._pending.popleft()
        event_task = asyncio.create_task(self._queue.get())
        stop_task = asyncio.create_task(stop.wait())
        done, pending = await asyncio.wait(
            {event_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if stop_task in done and stop_task.result():
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
            return None
        return event_task.result()

    async def _handle_turn(self, first: _ControlEvent) -> None:
        delta = first.delta
        if self.coalesce_seconds:
            await asyncio.sleep(self.coalesce_seconds)
        while True:
            try:
                event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if event.kind == "turn":
                delta += event.delta
            else:
                self._pending.append(event)
        scaled = delta * self.config.volume_step
        if scaled:
            await self.turn_action(scaled)
            await self.events.emit(
                "hardware_encoder_turned",
                {"detents": delta, "volume_delta": scaled},
            )

    async def run(self, stop: asyncio.Event) -> None:
        if not self.config.enabled:
            await self.state.set_component(
                "hardware_controls", "ok", "disabled", enabled=False
            )
            await stop.wait()
            return

        self._loop = asyncio.get_running_loop()
        try:
            self.input.open(
                lambda delta: self._from_gpio(_ControlEvent("turn", delta)),
                lambda: self._from_gpio(_ControlEvent("press")),
            )
        except Exception as exc:
            await self.state.set_component(
                "hardware_controls", "failed", str(exc), enabled=True
            )
            await self.events.emit(
                "hardware_controls_failed", {"error": str(exc)}
            )
            await stop.wait()
            return

        await self.state.set_component(
            "hardware_controls",
            "ok",
            "encoder ready",
            enabled=True,
            encoder_a_gpio=self.config.encoder_a_gpio,
            encoder_b_gpio=self.config.encoder_b_gpio,
            encoder_button_gpio=self.config.encoder_button_gpio,
        )
        await self.events.emit(
            "hardware_controls_started",
            {
                "encoder_a_gpio": self.config.encoder_a_gpio,
                "encoder_b_gpio": self.config.encoder_b_gpio,
                "encoder_button_gpio": self.config.encoder_button_gpio,
            },
        )
        try:
            while (event := await self._next_event(stop)) is not None:
                try:
                    if event.kind == "turn":
                        await self._handle_turn(event)
                    else:
                        await self.press_action()
                        await self.events.emit("hardware_encoder_pressed", {})
                except Exception as exc:
                    await self.events.emit(
                        "hardware_control_action_failed",
                        {"action": event.kind, "error": str(exc)},
                    )
        finally:
            self.input.close()
            self._loop = None
            await self.state.set_component(
                "hardware_controls", "ok", "stopped", enabled=True
            )
