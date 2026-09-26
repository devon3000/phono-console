import asyncio
from dataclasses import dataclass
from contextlib import suppress

from music_assistant_models.enums import PlaybackState

from phono_console.ma import MusicAssistantState
from phono_console.simulation import SimulatedEventSink


@dataclass
class FakePlayer:
    player_id: str = "console-id"
    name: str = "Phono Console"
    available: bool = True
    playback_state: PlaybackState = PlaybackState.PLAYING
    volume_level: int | None = 42
    group_volume: int | None = None


class FakePlayers:
    def __init__(self, players):
        self.players = players
        self.group_volume_calls: list[tuple[str, int]] = []

    def __iter__(self):
        return iter(self.players)

    async def group_volume(self, player_id: str, volume: int) -> None:
        self.group_volume_calls.append((player_id, volume))


class FakePlayerQueues:
    def __init__(self):
        self.play_calls: list[tuple[str, str]] = []
        self.stop_calls: list[str] = []

    async def play_media(self, queue_id, media, **kwargs):
        self.play_calls.append((queue_id, media))

    async def stop(self, queue_id):
        self.stop_calls.append(queue_id)


class FakeClient:
    def __init__(self):
        self.players = FakePlayers([FakePlayer()])
        self.player_queues = FakePlayerQueues()


def test_player_can_be_selected_by_name_or_id() -> None:
    state = MusicAssistantState("http://ma", None, "Phono Console", SimulatedEventSink())
    state._client = FakeClient()  # type: ignore[assignment]
    assert state._find_player().player_id == "console-id"
    state.console_player = "console-id"
    assert state._find_player().name == "Phono Console"


def test_play_vinyl_source_targets_named_players() -> None:
    async def scenario() -> None:
        events = SimulatedEventSink()
        state = MusicAssistantState("http://ma", None, "Phono Console", events)
        client = FakeClient()
        state._client = client  # type: ignore[assignment]
        state._ensure_connected = _noop  # type: ignore[method-assign]

        started = await state.play_vinyl_source(
            "source-client-id", ["Phono Console", "Missing Room"]
        )
        assert started == ["console-id"]
        queue_id, uri = client.player_queues.play_calls[0]
        assert queue_id == "console-id"
        assert uri == "sendspin_source://audio_source/source-client-id"
        assert ("ma_player_missing", {"player": "Missing Room"}) in events.events

        stopped = await state.stop_players(["console-id"])
        assert stopped == ["console-id"]
        assert client.player_queues.stop_calls == ["console-id"]

    asyncio.run(scenario())


def test_named_group_volume_is_clamped_and_sent() -> None:
    async def scenario() -> None:
        state = MusicAssistantState("http://ma", None, "Phono Console", SimulatedEventSink())
        client = FakeClient()
        state._client = client  # type: ignore[assignment]
        state._ensure_connected = _noop  # type: ignore[method-assign]

        assert await state.set_group_volume("Phono Console", 120)
        assert client.players.group_volume_calls == [("console-id", 100)]
        assert not await state.set_group_volume("Missing", 50)

    asyncio.run(scenario())


def test_named_group_volume_can_be_read() -> None:
    async def scenario() -> None:
        state = MusicAssistantState(
            "http://ma", None, "Phono Console", SimulatedEventSink()
        )
        client = FakeClient()
        state._client = client  # type: ignore[assignment]
        state._ensure_connected = _noop  # type: ignore[method-assign]
        client.players.players[0].group_volume = 37
        assert await state.get_player_volume("Phono Console") == 37
        assert await state.get_player_volume("Missing") is None

    asyncio.run(scenario())


def test_console_status_caches_ma_volume_for_route_handoff() -> None:
    async def scenario() -> None:
        state = MusicAssistantState(
            "http://ma", None, "Phono Console", SimulatedEventSink()
        )
        client = FakeClient()
        state._client = client  # type: ignore[assignment]
        ready = asyncio.Event()
        ready.set()
        state._ready = ready
        state._listener = asyncio.create_task(asyncio.sleep(10))
        try:
            assert await state.console_is_playing()
            assert state.console_volume == 42
        finally:
            state._listener.cancel()
            with suppress(asyncio.CancelledError):
                await state._listener

    asyncio.run(scenario())


async def _noop() -> None:
    return None


def test_connection_failure_is_degraded_with_backoff() -> None:
    async def scenario() -> None:
        events = SimulatedEventSink()
        state = MusicAssistantState("http://ma", None, "console", events)
        attempts = 0

        async def fail() -> None:
            nonlocal attempts
            attempts += 1
            raise OSError("offline")

        state._ensure_connected = fail  # type: ignore[method-assign]
        assert not await state.console_is_playing()
        await asyncio.sleep(0)
        assert not await state.console_is_playing()
        assert attempts == 1
        assert events.events[0][0] == "ma_connection_failed"

        await state.close()

    asyncio.run(scenario())


def test_connecting_to_ma_does_not_block_status_polling() -> None:
    async def scenario() -> None:
        events = SimulatedEventSink()
        state = MusicAssistantState("http://ma", None, "console", events)
        blocked = asyncio.Event()

        async def wait_forever() -> None:
            await blocked.wait()

        state._ensure_connected = wait_forever  # type: ignore[method-assign]
        assert not await asyncio.wait_for(state.console_is_playing(), timeout=0.1)
        assert state._connection_task is not None
        await state.close()

    asyncio.run(scenario())


def test_whole_house_request_is_explicit() -> None:
    async def scenario() -> None:
        state = MusicAssistantState("http://ma", None, "console", SimulatedEventSink())
        assert not await state.whole_house_is_requested()
        state.request_whole_house(True)
        assert await state.whole_house_is_requested()

    asyncio.run(scenario())
