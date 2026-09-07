from __future__ import annotations

import asyncio
import logging
import os
import signal

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
    api = ControlApi(
        state,
        api_token,
        whole_house_available=config.sendspin.source_enabled,
        whole_house_action=whole_house_action,
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
