import asyncio
from dataclasses import dataclass

from music_assistant_models.enums import PlaybackState

from phono_console.ma import MusicAssistantState
from phono_console.simulation import SimulatedEventSink


@dataclass
class FakePlayer:
    player_id: str = "console-id"
    name: str = "Phono Console"
    available: bool = True
    playback_state: PlaybackState = PlaybackState.PLAYING


class FakePlayers:
    def __init__(self, players):
        self.players = players

    def __iter__(self):
        return iter(self.players)


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
        assert uri == "sendspin://audio_source/source-client-id"
        assert ("ma_player_missing", {"player": "Missing Room"}) in events.events

        stopped = await state.stop_players(["console-id"])
        assert stopped == ["console-id"]
        assert client.player_queues.stop_calls == ["console-id"]

    asyncio.run(scenario())


async def _noop() -> None:
    return None


def test_whole_house_request_is_explicit() -> None:
    async def scenario() -> None:
        state = MusicAssistantState("http://ma", None, "console", SimulatedEventSink())
        assert not await state.whole_house_is_requested()
        state.request_whole_house(True)
        assert await state.whole_house_is_requested()

    asyncio.run(scenario())
