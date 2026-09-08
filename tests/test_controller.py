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


def test_controller_capture_failure_fails_silent_and_recovers() -> None:
    class FlakyMonitor:
        calls = 0

        async def level_dbfs(self) -> float:
            self.calls += 1
            if self.calls == 1:
                raise OSError("capture temporarily unavailable")
            return -120.0

        async def close(self) -> None:
            return None

    async def scenario() -> None:
        monitor = FlakyMonitor()
        events = SimulatedEventSink()
        stop = asyncio.Event()
        subject = Controller(
            config(),
            monitor,
            SimulatedMusicAssistant(),
            SimulatedAudioRouter(),
            events,
        )
        task = asyncio.create_task(subject.run(stop))
        while monitor.calls < 2:
            await asyncio.sleep(0.01)
        stop.set()
        await task
        assert not any(name == "controller_tick_failed" for name, _ in events.events)
        assert subject.route is Route.IDLE

    asyncio.run(scenario())


def test_capture_loss_immediately_stops_an_active_local_route() -> None:
    class DisconnectingMonitor:
        calls = 0

        async def level_dbfs(self) -> float:
            self.calls += 1
            if self.calls >= 3:
                raise RuntimeError("USB disconnected")
            return -20.0

        async def close(self) -> None:
            return None

    async def scenario() -> None:
        router = SimulatedAudioRouter()
        subject = Controller(
            config(),
            DisconnectingMonitor(),
            SimulatedMusicAssistant(),
            router,
            SimulatedEventSink(),
        )
        await subject.tick(now=0)
        await subject.tick(now=0.25)
        assert subject.route is Route.LOCAL_PHONO
        await subject.tick(now=0.3)
        assert subject.route is Route.IDLE
        assert router.routes[-1] is Route.IDLE

    asyncio.run(scenario())
