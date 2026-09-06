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


class FakeClient:
    def __init__(self):
        self.players = FakePlayers([FakePlayer()])


def test_player_can_be_selected_by_name_or_id() -> None:
    state = MusicAssistantState("http://ma", None, "Phono Console", SimulatedEventSink())
    state._client = FakeClient()  # type: ignore[assignment]
    assert state._find_player().player_id == "console-id"
    state.console_player = "console-id"
    assert state._find_player().name == "Phono Console"


def test_whole_house_request_is_explicit() -> None:
    async def scenario() -> None:
        state = MusicAssistantState("http://ma", None, "console", SimulatedEventSink())
        assert not await state.whole_house_is_requested()
        state.request_whole_house(True)
        assert await state.whole_house_is_requested()

    asyncio.run(scenario())
