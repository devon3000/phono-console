import asyncio
from dataclasses import replace
from pathlib import Path

from phono_console.config import load_config
from phono_console.controller import Controller
from phono_console.levels import ChannelLevel, LevelSession, StereoLevel
from phono_console.policy import PhonoOutputMode, Route
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


def test_controller_wakes_amplifier_on_first_active_route() -> None:
    async def scenario() -> None:
        level = SimulatedLevelMonitor()
        wakes = 0

        async def wake() -> None:
            nonlocal wakes
            wakes += 1

        subject = Controller(
            config(),
            level,
            SimulatedMusicAssistant(),
            SimulatedAudioRouter(),
            SimulatedEventSink(),
            activate_output=wake,
        )
        await subject.tick(now=0)
        level.level = -20
        await subject.tick(now=1)
        await subject.tick(now=1.25)
        await subject.tick(now=2)
        assert wakes == 1

    asyncio.run(scenario())


def test_controller_starts_hdmi_route_before_waking_amplifier() -> None:
    async def scenario() -> None:
        actions: list[str] = []

        class OrderedRouter(SimulatedAudioRouter):
            async def apply(self, route: Route) -> None:
                await super().apply(route)
                if route is not Route.IDLE:
                    actions.append("audio")

        async def wake() -> None:
            actions.append("wake")

        async def activate_route(_route: Route) -> None:
            actions.append("volume")

        level = SimulatedLevelMonitor()
        subject = Controller(
            config(),
            level,
            SimulatedMusicAssistant(),
            OrderedRouter(),
            SimulatedEventSink(),
            activate_output=wake,
            activate_route=activate_route,
        )
        await subject.tick(now=0)
        actions.clear()
        level.level = -20
        await subject.tick(now=1)
        await subject.tick(now=1.25)

        assert actions == ["audio", "wake", "volume"]

    asyncio.run(scenario())


def test_needle_drop_starts_hdmi_before_slow_ma_telemetry() -> None:
    async def scenario() -> None:
        class SlowMusicAssistant(SimulatedMusicAssistant):
            def __init__(self) -> None:
                super().__init__()
                self.query_started = asyncio.Event()
                self.allow_query = asyncio.Event()

            async def console_is_playing(self) -> bool:
                self.query_started.set()
                await self.allow_query.wait()
                return False

        class OrderedRouter(SimulatedAudioRouter):
            async def apply(self, route: Route) -> None:
                await super().apply(route)
                if route is Route.LOCAL_PHONO:
                    actions.append("audio")

        async def wake() -> None:
            actions.append("wake")

        base = config()
        fast_config = replace(
            base,
            detection=replace(base.detection, attack_ms=0),
        )
        actions: list[str] = []
        level = SimulatedLevelMonitor(level=-20)
        ma = SlowMusicAssistant()
        subject = Controller(
            fast_config,
            level,
            ma,
            OrderedRouter(),
            SimulatedEventSink(),
            activate_output=wake,
        )

        tick = asyncio.create_task(subject.tick(now=1))
        await ma.query_started.wait()
        assert actions == ["audio", "wake"]
        ma.allow_query.set()
        await tick
        assert subject.route is Route.LOCAL_PHONO

    asyncio.run(scenario())


def test_needle_drop_peak_wakes_above_noise_without_rms_activity() -> None:
    class PeakMonitor(SimulatedLevelMonitor):
        def __init__(self, peak: float) -> None:
            super().__init__(level=-70.0)
            channel = ChannelLevel(peak_dbfs=peak, rms_dbfs=-70.0, clipped=False)
            self.latest = StereoLevel(left=channel, right=channel)

    async def scenario() -> None:
        events = SimulatedEventSink()
        subject = Controller(
            config(),
            PeakMonitor(-44.0),
            SimulatedMusicAssistant(),
            SimulatedAudioRouter(),
            events,
        )
        status = await subject.tick(now=1)
        assert status.route is Route.LOCAL_PHONO
        assert status.phono_active
        assert (
            "phono_needle_drop_detected",
            {"peak_dbfs": -44.0, "threshold_dbfs": -45.0},
        ) in events.events

        noise = Controller(
            config(),
            PeakMonitor(-54.0),
            SimulatedMusicAssistant(),
            SimulatedAudioRouter(),
            SimulatedEventSink(),
        )
        noise_status = await noise.tick(now=1)
        assert noise_status.route is Route.IDLE
        assert not noise_status.phono_active

    asyncio.run(scenario())


def test_controller_activates_local_phono_profile_on_route_entry() -> None:
    async def scenario() -> None:
        level = SimulatedLevelMonitor()
        activated: list[Route] = []

        async def activate(route: Route) -> None:
            activated.append(route)

        subject = Controller(
            config(),
            level,
            SimulatedMusicAssistant(),
            SimulatedAudioRouter(),
            SimulatedEventSink(),
            activate_route=activate,
        )
        await subject.tick(now=0)
        level.level = -20
        await subject.tick(now=1)
        await subject.tick(now=1.25)

        assert Route.LOCAL_PHONO in activated

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


def test_active_phono_does_not_wait_for_idle_bluetooth_capture() -> None:
    class CountingBluetoothMonitor:
        def __init__(self) -> None:
            self.calls = 0

        async def level_dbfs(self) -> float:
            self.calls += 1
            return -120.0

        async def close(self) -> None:
            return None

    async def scenario() -> None:
        phono = SimulatedLevelMonitor()
        phono.level = -20.0
        bluetooth = CountingBluetoothMonitor()
        base = config()
        subject = Controller(
            replace(base, detection=replace(base.detection, attack_ms=250)),
            phono,
            SimulatedMusicAssistant(),
            SimulatedAudioRouter(),
            SimulatedEventSink(),
            bluetooth_monitor=bluetooth,
        )

        # A live phono signal has priority even while its attack timer settles.
        await subject.tick(now=0)
        assert bluetooth.calls == 0

        # Once phono owns the route, its meter cadence no longer depends on an
        # idle Bluetooth source producing frames.
        await subject.tick(now=0.25)
        await subject.tick(now=0.50)
        assert subject.route is Route.LOCAL_PHONO
        assert bluetooth.calls == 0

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
            phono_output_mode=lambda: PhonoOutputMode.DOWNSTAIRS,
        )
        await subject.tick(now=0)
        await subject.tick(now=0.25)
        assert subject.route is Route.DISTRIBUTED_PHONO
        assert prepared == ["phono"]

    asyncio.run(scenario())


def test_bluetooth_preempts_phono_release_hold_during_actual_silence() -> None:
    async def scenario() -> None:
        phono = SimulatedLevelMonitor(level=-20.0)
        bluetooth = SimulatedLevelMonitor(level=-120.0)
        subject = Controller(
            config(),
            phono,
            SimulatedMusicAssistant(),
            SimulatedAudioRouter(),
            SimulatedEventSink(),
            bluetooth_monitor=bluetooth,
        )

        await subject.tick(now=0)
        assert subject.route is Route.LOCAL_PHONO

        # Phono remains logically active for the record-flip release hold, but
        # real Bluetooth PCM should still be detected and take over.
        phono.level = -120.0
        bluetooth.level = -20.0
        await subject.tick(now=1)
        await subject.tick(now=1.25)
        assert subject.detector.active
        assert subject.route is Route.LOCAL_BLUETOOTH

    asyncio.run(scenario())


def test_bluetooth_media_playing_activates_route_without_pcm_threshold() -> None:
    async def scenario() -> None:
        playing = False
        bluetooth = SimulatedLevelMonitor(level=-120.0)
        subject = Controller(
            config(),
            SimulatedLevelMonitor(level=-120.0),
            SimulatedMusicAssistant(),
            SimulatedAudioRouter(),
            SimulatedEventSink(),
            bluetooth_monitor=bluetooth,
            bluetooth_is_playing=lambda: playing,
        )

        status = await subject.tick(now=0)
        assert status.route is Route.IDLE

        playing = True
        status = await subject.tick(now=0.1)
        assert status.bluetooth_active
        assert status.route is Route.LOCAL_BLUETOOTH

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

        base = config()
        subject = Controller(
            replace(base, detection=replace(base.detection, release_ms=5000)),
            level,
            SimulatedMusicAssistant(),
            SimulatedAudioRouter(),
            SimulatedEventSink(),
            distribution_available=lambda: distribution_requested,
            prepare_distribution=prepare,
            release_distribution=release,
            phono_output_mode=lambda: PhonoOutputMode.DOWNSTAIRS,
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


def test_distributed_bluetooth_releases_after_detector_hold() -> None:
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
        await subject.tick(now=16)
        assert subject.route is Route.IDLE
        assert released == [True]

    asyncio.run(scenario())


def test_timestamped_distribution_waits_silently_then_switches_to_ma() -> None:
    async def scenario() -> None:
        phono = SimulatedLevelMonitor()
        bluetooth = SimulatedLevelMonitor()
        bluetooth.level = -20.0
        ma = SimulatedMusicAssistant()
        router = SimulatedAudioRouter()
        ready = False
        prepared = []

        async def prepare(source):
            prepared.append(source.value)
            return ready

        subject = Controller(
            config(),
            phono,
            ma,
            router,
            SimulatedEventSink(),
            bluetooth_monitor=bluetooth,
            distribution_available=lambda: ready,
            distribution_capable=lambda source: source.value == "bluetooth",
            prepare_distribution=prepare,
        )
        await subject.tick(now=0)
        await subject.tick(now=0.25)
        assert subject.route is Route.IDLE
        assert prepared == ["bluetooth"]
        await subject.tick(now=1)
        assert prepared == ["bluetooth"]

        ready = True
        ma.playing = True
        await subject.tick(now=1.1)
        assert subject.route is Route.DISTRIBUTED_BLUETOOTH
        assert prepared == ["bluetooth", "bluetooth"]

    asyncio.run(scenario())


def test_timestamped_distribution_falls_back_locally_after_timeout() -> None:
    async def scenario() -> None:
        bluetooth = SimulatedLevelMonitor()
        bluetooth.level = -20.0
        subject = Controller(
            config(),
            SimulatedLevelMonitor(),
            SimulatedMusicAssistant(),
            SimulatedAudioRouter(),
            SimulatedEventSink(),
            bluetooth_monitor=bluetooth,
            distribution_available=lambda: False,
            distribution_capable=lambda _source: True,
            prepare_distribution=lambda _source: asyncio.sleep(0, result=False),
        )
        await subject.tick(now=0)
        await subject.tick(now=0.25)
        assert subject.route is Route.IDLE
        await subject.tick(now=5.3)
        assert subject.route is Route.LOCAL_BLUETOOTH

    asyncio.run(scenario())


def test_requested_phono_distribution_keeps_local_audio_until_ma_is_ready() -> None:
    async def scenario() -> None:
        phono = SimulatedLevelMonitor()
        phono.level = -20.0
        router = SimulatedAudioRouter()
        prepares: list[str] = []

        async def prepare(source):
            prepares.append(source.value)
            return False

        subject = Controller(
            config(),
            phono,
            SimulatedMusicAssistant(),
            router,
            SimulatedEventSink(),
            distribution_available=lambda: False,
            distribution_capable=lambda _source: True,
            prepare_distribution=prepare,
            phono_output_mode=lambda: PhonoOutputMode.DOWNSTAIRS,
        )
        await subject.tick(now=0)
        await subject.tick(now=0.25)
        assert subject.route is Route.LOCAL_PHONO
        assert prepares == ["phono"]

        # Explicit Downstairs mode keeps uninterrupted direct playback and
        # retries until the synchronized return feed is confirmed.
        await subject.tick(now=5.3)
        assert subject.route is Route.LOCAL_PHONO
        assert prepares == ["phono", "phono"]

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


def test_downstairs_phono_mode_expires_after_inactivity() -> None:
    async def scenario() -> None:
        current_mode = PhonoOutputMode.DOWNSTAIRS
        expirations = []

        async def expire() -> None:
            nonlocal current_mode
            current_mode = PhonoOutputMode.LOCAL
            expirations.append(True)

        base = config()
        subject = Controller(
            replace(
                base,
                routing=replace(base.routing, phono_mode_sticky_minutes=5),
            ),
            SimulatedLevelMonitor(),
            SimulatedMusicAssistant(),
            SimulatedAudioRouter(),
            SimulatedEventSink(),
            phono_output_mode=lambda: current_mode,
            expire_phono_output_mode=expire,
        )
        await subject.tick(now=0)
        await subject.tick(now=299)
        assert current_mode is PhonoOutputMode.DOWNSTAIRS
        await subject.tick(now=300)
        assert current_mode is PhonoOutputMode.LOCAL
        assert expirations == [True]

    asyncio.run(scenario())
