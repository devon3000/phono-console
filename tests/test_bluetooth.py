import asyncio

from phono_console.bluetooth import (
    BluetoothManager,
    map_transport_volume,
    parse_player_show,
    parse_transport_paths,
    parse_transport_volume,
)
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


def test_parse_player_show_extracts_transport_and_track_metadata() -> None:
    state = parse_player_show(
        """Player /org/bluez/hci0/dev_AA_BB/player0 [default]
        Name: iPhone
        Status: playing
        Position: 42000
        Track.Title: So What
        Track.Artist: Miles Davis
        Track.Album: Kind of Blue
        Track.Duration: 545000
        """
    )

    assert state["media_available"] is True
    assert state["media_status"] == "playing"
    assert state["media_position_ms"] == 42000
    assert state["media_track"] == {
        "title": "So What",
        "artist": "Miles Davis",
        "album": "Kind of Blue",
        "duration_ms": 545000,
    }


def test_parse_active_a2dp_sink_transport_volume() -> None:
    listing = "Transport /org/bluez/hci0/dev_AA_BB/fd0\n"
    details = """Transport /org/bluez/hci0/dev_AA_BB/fd0
        UUID: Audio Sink (0000110b-0000-1000-8000-00805f9b34fb)
        State: active
        Volume: 0x0040 (64)
    """
    assert parse_transport_paths(listing) == ["/org/bluez/hci0/dev_AA_BB/fd0"]
    assert parse_transport_volume(details) == 64
    assert parse_transport_volume(details.replace("active", "idle")) is None


def test_transport_volume_mapping_compresses_range_and_preserves_mute() -> None:
    assert map_transport_volume(0, 20, 80) == 0
    assert map_transport_volume(1, 20, 80) == 20
    assert map_transport_volume(64, 20, 80) == 50
    assert map_transport_volume(127, 20, 80) == 80


def test_transport_volume_changes_drive_shared_logical_volume() -> None:
    async def scenario() -> None:
        calls: list[int] = []

        async def command_runner(*args: str) -> tuple[int, str, str]:
            if args == ("transport.list",):
                return 0, "Transport /transport/0\n", ""
            if args == ("transport.show", "/transport/0"):
                return 0, "UUID: Audio Sink\nState: active\nVolume: 0x0040 (64)\n", ""
            return 1, "", "missing"

        async def volume_action(volume: int) -> None:
            calls.append(volume)

        state = StateStore()
        manager = BluetoothManager(
            BluetoothConfig(),
            SimulatedEventSink(),
            state,
            command_runner=command_runner,
            volume_action=volume_action,
        )
        await manager._refresh_transport_volume()
        await manager._refresh_transport_volume()

        assert calls == [50]
        assert state.bluetooth["bluetooth_volume"] == 50
        assert state.bluetooth["bluetooth_volume_raw"] == 64

    asyncio.run(scenario())


def test_media_command_uses_bluetoothctl_player_transport() -> None:
    async def scenario() -> None:
        calls: list[tuple[str, ...]] = []

        async def command_runner(*args: str) -> tuple[int, str, str]:
            calls.append(args)
            if args == ("player.show",):
                return 0, "Player /player/0\nStatus: paused\n", ""
            return 0, "", ""

        state = StateStore()
        manager = BluetoothManager(
            BluetoothConfig(),
            SimulatedEventSink(),
            state,
            command_runner=command_runner,
        )
        await manager.media_command("pause")

        assert calls == [("player.pause",), ("player.show",)]
        assert manager.playback_status == "paused"

    asyncio.run(scenario())


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
