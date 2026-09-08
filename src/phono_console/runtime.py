from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from importlib.metadata import PackageNotFoundError, version

from aiohttp import web

from .alsa import ArecordLevelMonitor
from .api import ControlApi, WholeHouseError
from .config import Config
from .controller import Controller
from .event_sinks import CompositeEventSink
from .events import LoggingEventSink
from .ma import MusicAssistantState
from .processes import ManagedProcess, ProcessSpec, SubprocessLauncher
from .router import ProcessAudioRouter
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


async def run_daemon(config: Config) -> None:
    state = StateStore()
    events = CompositeEventSink(LoggingEventSink(), state)
    launcher = SubprocessLauncher()
    router = ProcessAudioRouter(
        ManagedProcess(
            ProcessSpec("local_loopback", local_loopback_command(config)),
            launcher,
            events,
        )
    )
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
    controller = Controller(
        config, monitor, music_assistant, router, events, status_sink=state
    )

    publisher: SendspinSourcePublisher | None = None
    whole_house_action = None
    if config.sendspin.source_enabled:
        publisher = SendspinSourcePublisher(
            config.sendspin, config.audio, events, state
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
    api = ControlApi(
        state,
        api_token,
        whole_house_available=config.sendspin.source_enabled,
        whole_house_action=whole_house_action,
        level_reset_action=monitor.session.reset,
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
        tasks = [controller.run(stop)]
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
