from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import time
from contextlib import suppress
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version

from aiohttp import web

from .alsa import ArecordLevelMonitor
from .api import ControlApi, WholeHouseError
from .audio_engine_backend import TimestampedBluetoothBackend
from .amplifier import CecAmplifier
from .bluetooth import BluetoothManager
from .config import Config
from .controls import (
    HardwareControls,
    PressCommand,
    VolumeTarget,
    press_command_for_route,
    volume_target_for_route,
)
from .controller import Controller
from .event_sinks import CompositeEventSink
from .events import LoggingEventSink
from .ma import MusicAssistantState
from .processes import ManagedProcess, ProcessSpec, SubprocessLauncher
from .router import ProcessAudioRouter
from .policy import OutputTarget, PhonoOutputMode, Route, RoutingPhase, Source
from .sendspin_source import SendspinSourcePublisher
from .state import StateStore

LOGGER = logging.getLogger(__name__)


def system_info() -> dict[str, object]:
    hostname = socket.gethostname()
    addresses: set[str] = set()
    try:
        for result in socket.getaddrinfo(hostname, None):
            address = result[4][0]
            if not address.startswith("127.") and address != "::1":
                addresses.add(address)
    except OSError:
        pass
    # UDP connect performs no network exchange; it asks the kernel which local
    # interface would carry ordinary LAN traffic. This also works when the
    # hostname resolves only to Raspberry Pi OS's 127.0.1.1 entry.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route_socket:
            route_socket.connect(("192.0.2.1", 9))
            addresses.add(route_socket.getsockname()[0])
    except OSError:
        pass
    try:
        app_version = version("phono-console")
    except PackageNotFoundError:
        app_version = "development"
    return {
        "hostname": hostname,
        "addresses": sorted(addresses),
        "version": app_version,
    }


def local_loopback_command(config: Config) -> tuple[str, ...]:
    audio = config.audio
    return (
        "alsaloop",
        "-C",
        audio.capture_device,
        "-P",
        audio.playback_device,
        "-f",
        "S16_LE",
        "-r",
        str(audio.sample_rate),
        "-c",
        str(audio.channels),
        "-t",
        str(audio.target_latency_ms * 1000),
    )


def bluetooth_loopback_command(config: Config) -> tuple[str, ...]:
    audio = config.audio
    return (
        "alsaloop", "-C", config.bluetooth.capture_device,
        "-P", audio.playback_device, "-f", "S16_LE",
        "-r", str(audio.sample_rate), "-c", str(audio.channels),
        "-t", str(audio.target_latency_ms * 1000),
    )


def ma_loopback_command(config: Config) -> tuple[str, ...]:
    audio = config.audio
    return (
        "alsaloop", "-C", "console_ma_capture",
        "-P", audio.playback_device, "-f", "S16_LE",
        "-r", str(audio.sample_rate), "-c", str(audio.channels),
        "-t", str(audio.target_latency_ms * 1000),
    )


def distribution_needs_start(
    *, stream_requested: bool, console_playing: bool
) -> bool:
    """Return whether MA must be told to (re)start the distribution target."""
    # stream_requested describes the source capture command, not the state of
    # the target player/group.  It can remain true after MA has dissolved the
    # group, so it is insufficient on its own to suppress a new play request.
    return not stream_requested or not console_playing


async def run_daemon(config: Config) -> None:
    state = StateStore()
    output_target_path = Path(config.sendspin.state_dir) / "output-target"
    local_only_marker = Path(config.sendspin.state_dir) / "local-playback-only"
    phono_downstairs_marker = Path(config.sendspin.state_dir) / "phono-downstairs"
    local_phono_volume_path = Path(config.sendspin.state_dir) / "local-phono-volume"
    local_phono_volume = config.amplifier.local_phono_volume
    if local_phono_volume_path.exists():
        with suppress(ValueError):
            local_phono_volume = max(
                0, min(100, int(local_phono_volume_path.read_text().strip()))
            )
    sticky_seconds = config.routing.phono_mode_sticky_minutes * 60
    marker_is_fresh = (
        phono_downstairs_marker.exists()
        and time.time() - phono_downstairs_marker.stat().st_mtime < sticky_seconds
    )
    if phono_downstairs_marker.exists() and not marker_is_fresh:
        phono_downstairs_marker.unlink(missing_ok=True)
    if output_target_path.exists():
        with suppress(ValueError):
            state.output_target = OutputTarget(output_target_path.read_text().strip())
    elif local_only_marker.exists():
        state.output_target = OutputTarget.CONSOLE
    elif marker_is_fresh:
        state.output_target = OutputTarget.DOWNSTAIRS
    output_target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_target_path = output_target_path.with_suffix(".tmp")
    temporary_target_path.write_text(f"{state.output_target.value}\n")
    os.replace(temporary_target_path, output_target_path)
    local_only_marker.unlink(missing_ok=True)
    phono_downstairs_marker.unlink(missing_ok=True)
    events = CompositeEventSink(LoggingEventSink(), state)
    amplifier = CecAmplifier(config.amplifier, events, state)
    bluetooth_manager = BluetoothManager(config.bluetooth, events, state)
    launcher = SubprocessLauncher()
    timestamped_bluetooth = (
        TimestampedBluetoothBackend.create(config, events)
        if config.audio_engine.backend == "timestamped" and config.bluetooth.enabled
        else None
    )
    phono_loopback = ManagedProcess(
        ProcessSpec("local_loopback", local_loopback_command(config)),
        launcher,
        events,
    )
    bluetooth_loopback = (
        timestamped_bluetooth.playback
        if timestamped_bluetooth
        else (
            ManagedProcess(
                ProcessSpec(
                    "bluetooth_loopback", bluetooth_loopback_command(config)
                ),
                launcher,
                events,
            )
            if config.bluetooth.enabled
            else None
        )
    )
    ma_loopback = ManagedProcess(
        ProcessSpec("ma_loopback", ma_loopback_command(config)), launcher, events
    )
    router = ProcessAudioRouter(phono_loopback, bluetooth_loopback, ma_loopback)
    monitor = ArecordLevelMonitor(
        config.audio.capture_device,
        events,
        sample_rate=config.audio.sample_rate,
        channels=config.audio.channels,
        window_ms=config.audio.detection_window_ms,
    )
    ma_token = os.getenv(config.music_assistant.token_env) or None
    music_assistant = MusicAssistantState(
        config.music_assistant.base_url,
        ma_token,
        config.music_assistant.console_player,
        events,
        state,
        play_request_timeout_seconds=(
            config.routing.distribution_start_timeout_ms / 1000
        ),
    )

    async def bluetooth_volume_action(volume: int) -> None:
        route = state.status.route if state.status is not None else Route.IDLE
        if route is Route.DISTRIBUTED_BLUETOOTH:
            changed = await music_assistant.set_group_volume(
                config.routing.distribution_target, volume
            )
            if not changed:
                await amplifier.set_volume(volume)
        elif route is Route.LOCAL_BLUETOOTH:
            await amplifier.set_volume(volume)

    bluetooth_manager.set_volume_action(bluetooth_volume_action)

    async def activate_route(route: Route) -> None:
        if route is Route.LOCAL_PHONO:
            await amplifier.set_volume(local_phono_volume)
        elif route in {
            Route.MA_PLAYBACK,
            Route.DISTRIBUTED_PHONO,
            Route.DISTRIBUTED_BLUETOOTH,
        }:
            # MA may retain the Console player's volume across an idle/local
            # session and therefore send no new volume hook when playback
            # resumes. Explicitly conform the Yamaha at the route boundary so
            # the saved local-phono profile cannot leak into MA playback.
            ma_volume = music_assistant.console_volume
            if ma_volume is not None:
                await amplifier.set_volume(ma_volume)

    async def amplifier_volume_action(
        volume: int, source: str
    ) -> tuple[int, bool]:
        nonlocal local_phono_volume
        result = await amplifier.set_volume(volume)
        route = state.status.route if state.status is not None else Route.IDLE
        if source != "music_assistant" and route is Route.LOCAL_PHONO:
            local_phono_volume = volume
            local_phono_volume_path.parent.mkdir(parents=True, exist_ok=True)
            local_phono_volume_path.write_text(f"{volume}\n")
            await events.emit(
                "local_phono_volume_saved", {"volume": volume, "source": source}
            )
        return result
    publisher: SendspinSourcePublisher | None = None
    whole_house_action = None
    if config.sendspin.source_enabled:
        async def source_stopped(source: Source) -> None:
            # A bridge rebuild and an explicit MA stop are indistinguishable
            # at this layer. Treat source.stop as session telemetry; it must
            # never rewrite the user's persisted Console/Downstairs choice.
            await events.emit(
                "distribution_source_stopped",
                {
                    "source": source.value,
                    "requested_output": state.output_target.value,
                    "generation": state.routing_generation,
                },
            )

        publisher = SendspinSourcePublisher(
            config.sendspin,
            config.audio,
            events,
            state,
            source_devices={
                Source.PHONO: config.audio.capture_device,
                **(
                    {Source.BLUETOOTH: config.bluetooth.capture_device}
                    if config.bluetooth.enabled
                    else {}
                ),
            },
            pcm_stream_factories=(
                timestamped_bluetooth.pcm_stream_factories
                if timestamped_bluetooth is not None
                else None
            ),
            bluetooth_media=(
                bluetooth_manager if config.bluetooth.enabled else None
            ),
            source_stop_action=source_stopped,
            phono_activity_threshold_dbfs=(
                config.detection.phono_threshold_dbfs
            ),
            phono_activity_release_seconds=(
                config.detection.release_ms / 1000
            ),
            phono_activity_hysteresis_db=config.detection.hysteresis_db,
        )
        await publisher.set_distribution_enabled(
            state.output_target is OutputTarget.DOWNSTAIRS
        )
        await publisher.set_source_distribution_enabled(
            Source.PHONO,
            state.output_target is OutputTarget.DOWNSTAIRS,
        )
        await publisher.set_source_distribution_enabled(
            Source.BLUETOOTH,
            state.output_target is OutputTarget.DOWNSTAIRS,
        )

        async def whole_house_action(enabled: bool) -> None:
            assert publisher is not None
            if enabled:
                if publisher.client_id is None:
                    raise WholeHouseError(
                        "the vinyl source is not connected to the Sendspin "
                        "server yet"
                    )
                started = await music_assistant.play_vinyl_source(
                    publisher.client_id,
                    config.music_assistant.whole_house_players,
                )
                if not started:
                    raise WholeHouseError(
                        "none of the configured whole-house players were found"
                    )
            else:
                await music_assistant.stop_players(
                    config.music_assistant.whole_house_players
                )

    bluetooth_monitor = (
        timestamped_bluetooth.monitor
        if timestamped_bluetooth
        else (
            ArecordLevelMonitor(
                config.bluetooth.capture_device,
                events,
                sample_rate=config.audio.sample_rate,
                channels=config.audio.channels,
                window_ms=config.audio.detection_window_ms,
            )
            if config.bluetooth.enabled
            else None
        )
    )

    def distribution_available() -> bool:
        active_source = (
            Source.PHONO
            if state.status is not None and state.status.phono_active
            else (
                Source.BLUETOOTH
                if state.status is not None and state.status.bluetooth_active
                else Source.NONE
            )
        )
        phono_stop_grace = bool(
            state.output_target is OutputTarget.DOWNSTAIRS
            and publisher is not None
            and publisher.phono_stop_pending
            and state.status is not None
            and state.status.route is Route.DISTRIBUTED_PHONO
            and state.routing_session_generation == state.routing_generation
        )
        return bool(
            phono_stop_grace
            or (
                state.output_target is OutputTarget.DOWNSTAIRS
                and publisher is not None
                and state.sendspin_source.get("connected")
                and state.sendspin_source.get("stream_requested")
                and state.sendspin_source.get("streaming")
                and state.sendspin_source.get("selected_source")
                == active_source.value
                and publisher.stream_healthy
                and state.music_assistant.get("connected")
                and music_assistant.console_playing
                and music_assistant.player_is_playing(
                    config.routing.distribution_target
                )
                and state.routing_session_generation
                == state.routing_generation
                and state.routing_session_source is active_source
            )
        )

    def distribution_capable(source: Source) -> bool:
        return bool(
            publisher is not None
            and (
                source is Source.BLUETOOTH
                and timestamped_bluetooth is not None
                or source is Source.PHONO
                and state.output_target is OutputTarget.DOWNSTAIRS
                and not publisher.phono_stop_pending
            )
            and state.output_target is OutputTarget.DOWNSTAIRS
            and publisher.client_id is not None
            and state.sendspin_source.get("connected")
            and (
                not state.sendspin_source.get("stream_requested")
                or publisher.stream_healthy
            )
            and state.music_assistant.get("connected")
        )

    async def prepare_distribution(source: Source) -> bool:
        if publisher is None or publisher.client_id is None:
            return False
        if source is Source.PHONO and publisher.phono_stop_pending:
            return False
        await publisher.select_source(source)
        if distribution_needs_start(
            stream_requested=bool(
                state.sendspin_source.get("stream_requested")
            ),
            console_playing=music_assistant.console_playing,
        ):
            started = await music_assistant.play_vinyl_source(
                publisher.client_id, (config.routing.distribution_target,)
            )
            if not started:
                return False
        return bool(state.sendspin_source.get("stream_requested"))

    controller = Controller(
        config,
        monitor,
        music_assistant,
        router,
        events,
        status_sink=state,
        bluetooth_monitor=bluetooth_monitor,
        bluetooth_is_playing=(
            lambda: bluetooth_manager.playback_status == "playing"
            if bluetooth_manager is not None
            else False
        ),
        distribution_available=distribution_available,
        distribution_capable=distribution_capable,
        distribution_stream_healthy=(
            lambda: publisher.stream_healthy
            if publisher is not None
            else False
        ),
        prepare_distribution=prepare_distribution,
        release_distribution=lambda: music_assistant.stop_players(
            (config.routing.distribution_target,)
        ),
        phono_output_mode=lambda: state.phono_output_mode,
        activate_output=amplifier.power_on,
        activate_route=activate_route,
    )

    info = system_info()
    info.update(
        {
            "capture_device": config.audio.capture_device,
            "playback_device": config.audio.playback_device,
            "sample_rate": config.audio.sample_rate,
            "channels": config.audio.channels,
            "phono_mode_sticky_minutes": (
                config.routing.phono_mode_sticky_minutes
            ),
        }
    )
    await state.set_system_info(info)
    await state.set_component(
        "capture",
        "degraded",
        "waiting for first PCM sample",
        device=config.audio.capture_device,
    )
    await state.set_component(
        "local_output", "ok", "standby", device=config.audio.playback_device
    )
    await state.set_component(
        "sendspin_player", "degraded", "waiting for Music Assistant telemetry"
    )
    await state.set_component(
        "sendspin_source",
        "degraded" if config.sendspin.source_enabled else "ok",
        "connecting" if config.sendspin.source_enabled else "disabled",
    )
    await state.set_component(
        "amplifier",
        "ok",
        "Ready; state not queried" if config.amplifier.enabled else "Disabled",
        enabled=config.amplifier.enabled,
        powered=None,
        wake_on_audio=config.amplifier.wake_on_audio,
        physical_address=config.amplifier.physical_address,
        volume=None,
        muted=None,
    )
    await state.set_component(
        "hardware_controls",
        "degraded" if config.controls.enabled else "ok",
        "starting" if config.controls.enabled else "disabled",
        enabled=config.controls.enabled,
    )
    await amplifier.start()
    await state.set_music_assistant_state(
        {
            "connected": False,
            "server": config.music_assistant.base_url,
            "console_player": config.music_assistant.console_player,
        }
    )
    def reset_level_history() -> None:
        monitor.session.reset()
        if bluetooth_monitor is not None:
            bluetooth_monitor.session.reset()

    output_transition_lock = asyncio.Lock()

    def persist_output_target(target: OutputTarget) -> None:
        output_target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_target_path.with_suffix(".tmp")
        temporary.write_text(f"{target.value}\n")
        os.replace(temporary, output_target_path)

    async def output_mode_action(mode: PhonoOutputMode) -> None:
        """Serialize and apply the one authoritative output choice."""
        target = (
            OutputTarget.DOWNSTAIRS
            if mode is PhonoOutputMode.DOWNSTAIRS
            else OutputTarget.CONSOLE
        )
        async with output_transition_lock:
            generation = state.routing_generation
            if state.output_target is not target:
                return
            persist_output_target(target)

            status = state.status
            source = (
                Source.PHONO
                if status is not None and status.phono_active
                else (
                    Source.BLUETOOTH
                    if status is not None and status.bluetooth_active
                    else Source.NONE
                )
            )
            if target is OutputTarget.CONSOLE:
                if publisher is not None:
                    await publisher.set_distribution_enabled(False)
                    await publisher.set_source_distribution_enabled(
                        Source.PHONO, False
                    )
                    await publisher.set_source_distribution_enabled(
                        Source.BLUETOOTH, False
                    )
                try:
                    await music_assistant.stop_players(
                        (config.routing.distribution_target,)
                    )
                except Exception as exc:
                    await events.emit(
                        "output_remote_stop_unconfirmed", {"error": str(exc)}
                    )
                await state.set_routing_state(RoutingPhase.STABLE)
                return

            if publisher is None or publisher.client_id is None:
                await state.set_routing_state(
                    RoutingPhase.DEGRADED,
                    error="Sendspin source is not connected; playing locally",
                )
                return
            await publisher.set_distribution_enabled(True)
            await publisher.set_source_distribution_enabled(Source.PHONO, True)
            await publisher.set_source_distribution_enabled(Source.BLUETOOTH, True)
            if source is Source.NONE:
                await state.set_routing_state(RoutingPhase.STABLE)
                return
            await state.set_routing_state(
                RoutingPhase.STARTING_DISTRIBUTION,
                session_generation=generation,
                session_source=source,
            )
            await publisher.select_source(source)
            started = await music_assistant.play_vinyl_source(
                publisher.client_id, (config.routing.distribution_target,)
            )
            if generation != state.routing_generation:
                return
            if not started:
                await state.set_routing_state(
                    RoutingPhase.DEGRADED,
                    session_generation=generation,
                    session_source=source,
                    error=(
                        f"{config.routing.distribution_target} was not found; "
                        "playing locally"
                    ),
                )

    async def hardware_volume_action(delta: int) -> None:
        status = state.status
        route = status.route if status is not None else Route.IDLE
        target_kind = volume_target_for_route(route)
        if target_kind is VolumeTarget.DOWNSTAIRS:
            current = await music_assistant.get_player_volume(
                config.routing.distribution_target
            )
            if current is None:
                raise RuntimeError(
                    f"volume unavailable for {config.routing.distribution_target}"
                )
            target = max(0, min(100, current + delta))
            if not await music_assistant.set_group_volume(
                config.routing.distribution_target, target
            ):
                raise RuntimeError(
                    f"player not found: {config.routing.distribution_target}"
                )
            await events.emit(
                "hardware_volume_changed",
                {
                    "target": "music_assistant",
                    "player": config.routing.distribution_target,
                    "volume": target,
                },
            )
            return

        if target_kind is VolumeTarget.NONE:
            await events.emit(
                "hardware_volume_ignored", {"route": route.value}
            )
            return
        if route is Route.LOCAL_PHONO:
            current = local_phono_volume
        else:
            amplifier_state = state.components.get("amplifier", {})
            reported = amplifier_state.get("volume")
            current = int(reported) if reported is not None else 50
        volume, muted = await amplifier_volume_action(
            max(0, min(100, current + delta)), "hardware_encoder"
        )
        await events.emit(
            "hardware_volume_changed",
            {"target": "yamaha", "volume": volume, "muted": muted},
        )

    async def hardware_press_action() -> None:
        status = state.status
        route = status.route if status is not None else Route.IDLE
        command = press_command_for_route(route)
        if command is PressCommand.PHONO_DOWNSTAIRS:
            await state.set_output_target(OutputTarget.DOWNSTAIRS)
            await output_mode_action(PhonoOutputMode.DOWNSTAIRS)
            await events.emit(
                "phono_output_mode_changed",
                {
                    "mode": PhonoOutputMode.DOWNSTAIRS.value,
                    "source": "hardware_encoder",
                },
            )
        elif command is PressCommand.PHONO_LOCAL:
            await state.set_output_target(OutputTarget.CONSOLE)
            await output_mode_action(PhonoOutputMode.LOCAL)
            await events.emit(
                "phono_output_mode_changed",
                {
                    "mode": PhonoOutputMode.LOCAL.value,
                    "source": "hardware_encoder",
                },
            )
        elif command is PressCommand.STOP_BLUETOOTH:
            if route is Route.DISTRIBUTED_BLUETOOTH:
                await music_assistant.stop_players(
                    (config.routing.distribution_target,)
                )
                await bluetooth_manager.media_command("pause")
            else:
                await bluetooth_manager.media_command("pause")
        elif command is PressCommand.STOP_MA:
            await music_assistant.stop_players(
                (config.routing.distribution_target,)
            )
        else:
            await events.emit(
                "hardware_button_ignored", {"route": route.value}
            )

    hardware_controls = HardwareControls(
        config.controls,
        events,
        state,
        hardware_volume_action,
        hardware_press_action,
    )

    api = ControlApi(
        state,
        None,
        whole_house_available=config.sendspin.source_enabled,
        whole_house_action=whole_house_action,
        level_reset_action=reset_level_history,
        pairing_open_action=(
            bluetooth_manager.open_pairing if config.bluetooth.enabled else None
        ),
        pairing_close_action=(
            bluetooth_manager.close_pairing if config.bluetooth.enabled else None
        ),
        bluetooth_device_action=(
            bluetooth_manager.device_action if config.bluetooth.enabled else None
        ),
        bluetooth_media_action=(
            bluetooth_manager.media_command if config.bluetooth.enabled else None
        ),
        amplifier_volume_action=(
            amplifier_volume_action if config.amplifier.enabled else None
        ),
        output_mode_action=output_mode_action,
    )
    # Dashboard polling happens four times per second and otherwise buries the
    # routing/audio events that matter in the appliance journal.
    runner = web.AppRunner(api.application(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.runtime.api_host, config.runtime.api_port)
    await site.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            pass

    await events.emit(
        "daemon_started",
        {"api_host": config.runtime.api_host, "api_port": config.runtime.api_port},
    )
    try:
        tasks = [
            controller.run(stop),
            bluetooth_manager.run(stop),
            hardware_controls.run(stop),
        ]
        if timestamped_bluetooth is not None:
            tasks.append(timestamped_bluetooth.run(stop, state))
        if publisher is not None:
            tasks.append(publisher.run(stop))
        await asyncio.gather(*tasks)
    finally:
        await amplifier.close()
        await runner.cleanup()
        await events.emit("daemon_stopped", {})


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
