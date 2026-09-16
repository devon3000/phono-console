import asyncio

from phono_console.bluetooth import BluetoothManager
from phono_console.config import BluetoothConfig
from phono_console.simulation import SimulatedEventSink
from phono_console.state import StateStore


class FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, value: bytes) -> None:
        self.writes.append(value)

    async def drain(self) -> None:
        return None


class FakeProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.stdin = FakeStdin()


def test_disabled_bluetooth_manager_degrades_nothing() -> None:
    async def scenario() -> None:
        state = StateStore()
        manager = BluetoothManager(
            BluetoothConfig(enabled=False), SimulatedEventSink(), state
        )
        await manager.run(asyncio.Event())
        assert state.bluetooth == {
            "enabled": False,
            "agent": False,
            "pairing": False,
        }

    asyncio.run(scenario())


def test_open_pairing_uses_existing_agent_without_waiting_for_subprocess() -> None:
    async def scenario() -> None:
        state = StateStore()
        manager = BluetoothManager(
            BluetoothConfig(pairing_window_seconds=120),
            SimulatedEventSink(),
            state,
        )
        process = FakeProcess()
        manager._process = process  # type: ignore[assignment]

        await manager.open_pairing()

        assert process.stdin.writes == [b"pairable on\n", b"discoverable on\n"]
        assert state.bluetooth["pairing"] is True
        assert state.bluetooth["pairing_seconds"] == 120
        assert manager._pairing_task is not None
        manager._pairing_task.cancel()
        with suppress(asyncio.CancelledError):
            await manager._pairing_task

    from contextlib import suppress

    asyncio.run(scenario())


def test_agent_prompts_are_accepted_only_during_pairing_window() -> None:
    async def scenario() -> None:
        manager = BluetoothManager(
            BluetoothConfig(), SimulatedEventSink(), StateStore()
        )
        process = FakeProcess()
        manager._process = process  # type: ignore[assignment]

        manager._pairing_active = True
        await manager._answer_agent_prompt(True, "Confirm passkey (yes/no)")
        manager._pairing_active = False
        await manager._answer_agent_prompt(False, "Authorize service (yes/no)")

        assert process.stdin.writes == [b"yes\n", b"no\n"]

    asyncio.run(scenario())
