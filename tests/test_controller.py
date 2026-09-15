import asyncio
from pathlib import Path

from phono_console.config import load_config
from phono_console.controller import Controller
from phono_console.levels import ChannelLevel, LevelSession, StereoLevel
from phono_console.policy import Route
from phono_console.state import StateStore
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
        assert router.routes[-1] is Route.LOCAL_PHONO
        assert [event for event, _ in events.events] == [
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


def test_requested_distribution_is_prepared_immediately() -> None:
    async def scenario() -> None:
        level = SimulatedLevelMonitor()
        level.level = -20.0
        router = SimulatedAudioRouter()
        prepared = []

        async def prepare(source):
            prepared.append(source.value)
            return True

        subject = Controller(
            config(),
            level,
            SimulatedMusicAssistant(),
            router,
            SimulatedEventSink(),
            distribution_available=lambda: True,
            prepare_distribution=prepare,
        )
        await subject.tick(now=0)
        await subject.tick(now=0.25)
        assert subject.route is Route.DISTRIBUTED_PHONO
        assert prepared == ["phono"]

    asyncio.run(scenario())


def test_distribution_is_released_when_source_signal_ends() -> None:
    async def scenario() -> None:
        level = SimulatedLevelMonitor()
        level.level = -20.0
        released = []
        distribution_requested = True

        async def prepare(_source):
            return True

        async def release():
            released.append(True)

        subject = Controller(
            config(),
            level,
            SimulatedMusicAssistant(),
            SimulatedAudioRouter(),
            SimulatedEventSink(),
            distribution_available=lambda: distribution_requested,
            prepare_distribution=prepare,
            release_distribution=release,
        )
        await subject.tick(now=0)
        await subject.tick(now=10)
        assert subject.route is Route.DISTRIBUTED_PHONO
        level.level = -120.0
        await subject.tick(now=16)
        # The Sendspin source session remains latched until MA acknowledges
        # line-sense off with source.stop.
        assert subject.route is Route.DISTRIBUTED_PHONO
        distribution_requested = False
        await subject.tick(now=22)
        assert subject.route is Route.IDLE
        assert released == [True]

    asyncio.run(scenario())


def test_distributed_bluetooth_stays_latched_during_detector_gap() -> None:
    async def scenario() -> None:
        phono = SimulatedLevelMonitor()
        bluetooth = SimulatedLevelMonitor()
        bluetooth.level = -20.0
        ma = SimulatedMusicAssistant()
        router = SimulatedAudioRouter()
        released = []

        async def prepare(_source):
            return True

        async def release():
            released.append(True)

        subject = Controller(
            config(),
            phono,
            ma,
            router,
            SimulatedEventSink(),
            bluetooth_monitor=bluetooth,
            distribution_available=lambda: True,
            prepare_distribution=prepare,
            release_distribution=release,
        )
        await subject.tick(now=0)
        await subject.tick(now=10)
        assert subject.route is Route.DISTRIBUTED_BLUETOOTH

        # MA is now rendering the returned source, while a temporary silence
        # window makes the local Bluetooth detector release.
        ma.playing = True
        bluetooth.level = -120.0
        await subject.tick(now=13)
        assert subject.route is Route.DISTRIBUTED_BLUETOOTH
        assert released == []

    asyncio.run(scenario())


def test_selected_bluetooth_source_drives_dashboard_input_levels() -> None:
    class MeterMonitor:
        def __init__(self, dbfs: float) -> None:
            self.latest = StereoLevel(
                ChannelLevel(dbfs, dbfs - 1, False),
                ChannelLevel(dbfs - 2, dbfs - 3, False),
            )
            self.session = LevelSession()
            self.session.update(self.latest)

        async def level_dbfs(self) -> float:
            return self.latest.left.rms_dbfs

        async def close(self) -> None:
            return None

    async def scenario() -> None:
        state = StateStore()
        subject = Controller(
            config(),
            MeterMonitor(-60.0),
            SimulatedMusicAssistant(),
            SimulatedAudioRouter(),
            SimulatedEventSink(),
            status_sink=state,
            bluetooth_monitor=MeterMonitor(-10.0),
        )

        await subject.tick(now=0)
        await subject.tick(now=1)

        assert subject.route is Route.LOCAL_BLUETOOTH
        assert state.input_levels["left"]["peak_dbfs"] == -10.0
        assert state.input_levels["right"]["peak_dbfs"] == -12.0

    asyncio.run(scenario())
