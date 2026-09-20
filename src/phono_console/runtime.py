from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import time
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version

from aiohttp import web

from .alsa import ArecordLevelMonitor
from .api import ControlApi, WholeHouseError
from .audio_engine_backend import TimestampedBluetoothBackend
from .bluetooth import BluetoothManager
from .config import Config
from .controller import Controller
from .event_sinks import CompositeEventSink
from .events import LoggingEventSink
from .ma import MusicAssistantState
from .processes import ManagedProcess, ProcessSpec, SubprocessLauncher
from .router import ProcessAudioRouter
from .policy import PhonoOutputMode, Route, Source
from .sendspin_source import SendspinSourcePublisher
from .state import StateStore

LOGGER = logging.getLogger(__name__)


def validate_api_security(host: str, token: str | None) -> None:
    """Refuse an unauthenticated API exposed beyond the local machine."""
    if host not in {"127.0.0.1", "::1", "localhost"} and token is None:
        raise RuntimeError(
            "PHONO_CONSOLE_API_TOKEN must be set when the API listens on the network"
        )


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


async def run_daemon(config: Config) -> None:
    state = StateStore()
    local_only_marker = Path(config.sendspin.state_dir) / "local-playback-only"
    phono_downstairs_marker = Path(config.sendspin.state_dir) / "phono-downstairs"
    await state.set_local_playback_only(local_only_marker.exists())
    sticky_seconds = config.routing.phono_mode_sticky_minutes * 60
    marker_is_fresh = (
        phono_downstairs_marker.exists()
        and time.time() - phono_downstairs_marker.stat().st_mtime < sticky_seconds
    )
    if phono_downstairs_marker.exists() and not marker_is_fresh:
        phono_downstairs_marker.unlink(missing_ok=True)
    await state.set_phono_output_mode(
        PhonoOutputMode.DOWNSTAIRS if marker_is_fresh else PhonoOutputMode.LOCAL
    )
    events = CompositeEventSink(LoggingEventSink(), state)
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
    )
    publisher: SendspinSourcePublisher | None = None
    whole_house_action = None
    if config.sendspin.source_enabled:
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
        )
        await publisher.set_distribution_enabled(not state.local_playback_only)
        await publisher.set_source_distribution_enabled(
            Source.PHONO,
            state.phono_output_mode is PhonoOutputMode.DOWNSTAIRS,
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
        return bool(
            not state.local_playback_only
            and publisher is not None
            and state.sendspin_source.get("connected")
            and state.sendspin_source.get("stream_requested")
            and state.music_assistant.get("connected")
            and music_assistant.console_playing
        )

    def distribution_capable(source: Source) -> bool:
        return bool(
            (
                source is Source.BLUETOOTH
                and timestamped_bluetooth is not None
                or source is Source.PHONO
                and state.phono_output_mode is PhonoOutputMode.DOWNSTAIRS
            )
            and not state.local_playback_only
            and publisher is not None
            and publisher.client_id is not None
            and state.sendspin_source.get("connected")
            and state.music_assistant.get("connected")
        )

    async def prepare_distribution(source: Source) -> bool:
        if publisher is None or publisher.client_id is None:
            return False
        await publisher.select_source(source)
        if not state.sendspin_source.get("stream_requested"):
            started = await music_assistant.play_vinyl_source(
                publisher.client_id, (config.routing.distribution_target,)
            )
            if not started:
                return False
        return bool(state.sendspin_source.get("stream_requested"))

    async def expire_phono_output_mode() -> None:
        phono_downstairs_marker.unlink(missing_ok=True)
        await state.set_phono_output_mode(PhonoOutputMode.LOCAL)
        if publisher is not None:
            await publisher.set_source_distribution_enabled(Source.PHONO, False)
        if (
            state.status is not None
            and state.status.route is Route.DISTRIBUTED_PHONO
        ):
            try:
                await music_assistant.stop_players(
                    (config.routing.distribution_target,)
                )
            except Exception as exc:
                await events.emit(
                    "phono_output_remote_stop_unconfirmed", {"error": str(exc)}
                )
        await events.emit(
            "phono_output_mode_expired",
            {"mode": PhonoOutputMode.LOCAL.value},
        )

    async def refresh_phono_output_mode() -> None:
        # mtime is the restart-safe last-activity timestamp. Refresh at most
        # once per minute while a record is active (enforced by Controller).
        phono_downstairs_marker.touch()

    controller = Controller(
        config,
        monitor,
        music_assistant,
        router,
        events,
        status_sink=state,
        bluetooth_monitor=bluetooth_monitor,
        distribution_available=distribution_available,
        distribution_capable=distribution_capable,
        prepare_distribution=prepare_distribution,
        release_distribution=lambda: music_assistant.stop_players(
            (config.routing.distribution_target,)
        ),
        phono_output_mode=lambda: state.phono_output_mode,
        expire_phono_output_mode=expire_phono_output_mode,
        refresh_phono_output_mode=refresh_phono_output_mode,
    )

    api_token = os.getenv(config.runtime.api_token_env) or None
    validate_api_security(config.runtime.api_host, api_token)
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

    async def local_only_action(enabled: bool) -> None:
        local_only_marker.parent.mkdir(parents=True, exist_ok=True)
        if enabled:
            local_only_marker.touch()
        else:
            local_only_marker.unlink(missing_ok=True)
        if publisher is not None:
            await publisher.set_distribution_enabled(not enabled)
        if enabled:
            try:
                await music_assistant.stop_players(
                    (config.routing.distribution_target,)
                )
            except Exception as exc:
                # Local-only is specifically an outage/troubleshooting mode;
                # it must still engage when MA cannot confirm the remote stop.
                await events.emit(
                    "local_only_remote_stop_unconfirmed", {"error": str(exc)}
                )

    async def phono_output_action(mode: PhonoOutputMode) -> None:
        phono_downstairs_marker.parent.mkdir(parents=True, exist_ok=True)
        if mode is PhonoOutputMode.DOWNSTAIRS:
            if state.local_playback_only:
                raise WholeHouseError("turn off Local Only before using Downstairs")
            if publisher is None or publisher.client_id is None:
                raise WholeHouseError("Sendspin source is not connected")
            await publisher.set_source_distribution_enabled(Source.PHONO, True)
            # Choosing the mode while idle only changes the sticky preference.
            # If a record is already playing, move it immediately; otherwise
            # the controller starts distribution when it first detects phono.
            if state.status is not None and state.status.phono_active:
                # Fail-safe handoff: release direct monitoring before asking
                # MA to start a delayed synchronized path. The controller sees
                # the already-published DOWNSTAIRS mode and keeps it muted
                # until the Sendspin return path is confirmed.
                await router.apply(Route.IDLE)
                await publisher.select_source(Source.PHONO)
                started = await music_assistant.play_vinyl_source(
                    publisher.client_id, (config.routing.distribution_target,)
                )
                if not started:
                    await publisher.set_source_distribution_enabled(
                        Source.PHONO, False
                    )
                    raise WholeHouseError(
                        "Downstairs was not found in Music Assistant"
                    )
            phono_downstairs_marker.touch()
        else:
            phono_downstairs_marker.unlink(missing_ok=True)
            if publisher is not None:
                await publisher.set_source_distribution_enabled(Source.PHONO, False)
            # Do not interrupt Bluetooth merely because the phono preference
            # changed while Bluetooth owns the shared Downstairs target.
            if (
                state.status is not None
                and state.status.route is Route.DISTRIBUTED_PHONO
            ):
                try:
                    await music_assistant.stop_players(
                        (config.routing.distribution_target,)
                    )
                except Exception as exc:
                    await events.emit(
                        "phono_output_remote_stop_unconfirmed", {"error": str(exc)}
                    )

    api = ControlApi(
        state,
        api_token,
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
        local_only_action=local_only_action,
        phono_output_action=phono_output_action,
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
        tasks = [controller.run(stop), bluetooth_manager.run(stop)]
        if timestamped_bluetooth is not None:
            tasks.append(timestamped_bluetooth.run(stop, state))
        if publisher is not None:
            tasks.append(publisher.run(stop))
        await asyncio.gather(*tasks)
    finally:
        await runner.cleanup()
        await events.emit("daemon_stopped", {})


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
