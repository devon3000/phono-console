import asyncio

from phono_console.config import ControlsConfig
from phono_console.controls import (
    HardwareControls,
    PressCommand,
    VolumeTarget,
    press_command_for_route,
    volume_target_for_route,
)
from phono_console.policy import Route
from phono_console.simulation import SimulatedEventSink
from phono_console.state import StateStore


class FakeEncoderInput:
    def __init__(self) -> None:
        self.on_turn = None
        self.on_press = None
        self.opened = False
        self.closed = False

    def open(self, on_turn, on_press) -> None:
        self.opened = True
        self.on_turn = on_turn
        self.on_press = on_press

    def close(self) -> None:
        self.closed = True

    def turn(self, delta: int) -> None:
        assert self.on_turn is not None
        self.on_turn(delta)

    def press(self) -> None:
        assert self.on_press is not None
        self.on_press()


def test_route_control_semantics() -> None:
    assert volume_target_for_route(Route.LOCAL_PHONO) is VolumeTarget.LOCAL
    assert volume_target_for_route(Route.LOCAL_BLUETOOTH) is VolumeTarget.LOCAL
    assert volume_target_for_route(Route.DISTRIBUTED_PHONO) is VolumeTarget.DOWNSTAIRS
    assert volume_target_for_route(Route.DISTRIBUTED_BLUETOOTH) is VolumeTarget.DOWNSTAIRS
    assert volume_target_for_route(Route.MA_PLAYBACK) is VolumeTarget.DOWNSTAIRS
    assert volume_target_for_route(Route.IDLE) is VolumeTarget.NONE

    assert press_command_for_route(Route.LOCAL_PHONO) is PressCommand.PHONO_DOWNSTAIRS
    assert press_command_for_route(Route.DISTRIBUTED_PHONO) is PressCommand.PHONO_LOCAL
    assert press_command_for_route(Route.LOCAL_BLUETOOTH) is PressCommand.STOP_BLUETOOTH
    assert press_command_for_route(Route.DISTRIBUTED_BLUETOOTH) is PressCommand.STOP_BLUETOOTH
    assert press_command_for_route(Route.MA_PLAYBACK) is PressCommand.STOP_MA
    assert press_command_for_route(Route.IDLE) is PressCommand.NONE


def test_hardware_controls_coalesce_turns_and_dispatch_press() -> None:
    async def scenario() -> None:
        turns: list[int] = []
        presses = 0

        async def turn(delta: int) -> None:
            turns.append(delta)

        async def press() -> None:
            nonlocal presses
            presses += 1

        encoder = FakeEncoderInput()
        state = StateStore()
        events = SimulatedEventSink()
        controls = HardwareControls(
            ControlsConfig(enabled=True, volume_step=2),
            events,
            state,
            turn,
            press,
            input_device=encoder,
            coalesce_seconds=0.01,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(controls.run(stop))
        await asyncio.sleep(0)
        assert encoder.opened

        encoder.turn(1)
        encoder.turn(1)
        encoder.turn(-1)
        encoder.press()
        await asyncio.sleep(0.05)
        assert turns == [2]
        assert presses == 1
        assert state.components["hardware_controls"]["message"] == "encoder ready"

        stop.set()
        await asyncio.wait_for(task, timeout=1)
        assert encoder.closed
        names = [name for name, _ in events.events]
        assert "hardware_controls_started" in names
        assert "hardware_encoder_turned" in names
        assert "hardware_encoder_pressed" in names

    asyncio.run(scenario())


def test_disabled_hardware_controls_never_open_gpio() -> None:
    async def scenario() -> None:
        encoder = FakeEncoderInput()

        async def unused_turn(_delta: int) -> None:
            raise AssertionError

        async def unused_press() -> None:
            raise AssertionError

        stop = asyncio.Event()
        controls = HardwareControls(
            ControlsConfig(enabled=False),
            SimulatedEventSink(),
            StateStore(),
            unused_turn,
            unused_press,
            input_device=encoder,
        )
        task = asyncio.create_task(controls.run(stop))
        await asyncio.sleep(0)
        assert not encoder.opened
        stop.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(scenario())
