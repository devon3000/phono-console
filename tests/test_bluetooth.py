import asyncio

from phono_console.bluetooth import BluetoothManager
from phono_console.config import BluetoothConfig
from phono_console.simulation import SimulatedEventSink
from phono_console.state import StateStore


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
