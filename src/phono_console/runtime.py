from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version

from aiohttp import web

from .alsa import ArecordLevelMonitor
from .api import ControlApi, WholeHouseError
from .bluetooth import BluetoothManager
from .config import Config
from .controller import Controller
from .event_sinks import CompositeEventSink
from .events import LoggingEventSink
from .ma import MusicAssistantState
from .processes import ManagedProcess, ProcessSpec, SubprocessLauncher
from .router import ProcessAudioRouter
from .policy import Source
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
    await state.set_local_playback_only(local_only_marker.exists())
    events = CompositeEventSink(LoggingEventSink(), state)
    launcher = SubprocessLauncher()
    phono_loopback = ManagedProcess(
            ProcessSpec("local_loopback", local_loopback_command(config)),
            launcher,
            events,
        )
    bluetooth_loopback = (
        ManagedProcess(
            ProcessSpec("bluetooth_loopback", bluetooth_loopback_command(config)),
            launcher,
            events,
        )
        if config.bluetooth.enabled
        else None
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
        )
        await publisher.set_distribution_enabled(not state.local_playback_only)

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

    def distribution_available() -> bool:
        return bool(
            not state.local_playback_only
            and publisher is not None
            and state.sendspin_source.get("connected")
            and state.sendspin_source.get("stream_requested")
            and state.music_assistant.get("connected")
        )

    async def prepare_distribution(source: Source) -> bool:
        if publisher is None or publisher.client_id is None:
            return False
        await publisher.select_source(source)
        return bool(state.sendspin_source.get("stream_requested"))

    controller = Controller(
        config,
        monitor,
        music_assistant,
        router,
        events,
        status_sink=state,
        bluetooth_monitor=bluetooth_monitor,
        distribution_available=distribution_available,
        prepare_distribution=prepare_distribution,
        release_distribution=lambda: music_assistant.stop_players(
            (config.routing.distribution_target,)
        ),
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
    bluetooth_manager = BluetoothManager(config.bluetooth, events, state)

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

    api = ControlApi(
        state,
        api_token,
        whole_house_available=config.sendspin.source_enabled,
        whole_house_action=whole_house_action,
        level_reset_action=monitor.session.reset,
        pairing_open_action=(
            bluetooth_manager.open_pairing if config.bluetooth.enabled else None
        ),
        pairing_close_action=(
            bluetooth_manager.close_pairing if config.bluetooth.enabled else None
        ),
        bluetooth_device_action=(
            bluetooth_manager.device_action if config.bluetooth.enabled else None
        ),
        local_only_action=local_only_action,
    )
    runner = web.AppRunner(api.application())
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
