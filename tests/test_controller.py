import asyncio
from pathlib import Path

from phono_console.config import load_config
from phono_console.controller import Controller
from phono_console.policy import Route
from phono_console.simulation import (
    SimulatedAudioRouter,
    SimulatedEventSink,
    SimulatedLevelMonitor,
    SimulatedMusicAssistant,
)


def config():
    path = Path(__file__).parents[1] / "config" / "phono-console.example.toml"
    return load_config(path)


def test_controller_applies_only_changed_routes() -> None:
    async def scenario() -> None:
        level = SimulatedLevelMonitor()
        ma = SimulatedMusicAssistant()
        router = SimulatedAudioRouter()
        events = SimulatedEventSink()
        subject = Controller(config(), level, ma, router, events)

        await subject.tick(now=0)
        await subject.tick(now=1)
        assert router.routes == [Route.IDLE]

        level.level = -30
        await subject.tick(now=2)
        await subject.tick(now=2.25)
        assert router.routes[-1] is Route.LOCAL_PHONO

        ma.playing = True
        await subject.tick(now=3)
        assert router.routes[-1] is Route.MA_PLAYBACK
        assert [event for event, _ in events.events] == [
            "route_changed",
            "route_changed",
            "route_changed",
        ]

    asyncio.run(scenario())
