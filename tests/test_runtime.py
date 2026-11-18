from pathlib import Path

import pytest

from phono_console.config import load_config
from phono_console.policy import OutputTarget, Source
from phono_console.state import StateStore

from phono_console.runtime import (
    distribution_is_ready,
    distribution_needs_start,
    local_loopback_command,
    ma_loopback_command,
    system_info,
)


@pytest.mark.parametrize("missing", [
    None, "connected", "stream_requested", "streaming", "selected_source",
    "stream_healthy", "console_playing", "target_playing", "publisher_present",
    "generation", "session_source", "ma_connected", "console_only", "no_source",
])
def test_distribution_readiness_keeps_session_safety_checks(missing) -> None:
    state = StateStore()
    state.output_target = OutputTarget.DOWNSTAIRS
    state.routing_generation = state.routing_session_generation = 2
    state.routing_session_source = Source.BLUETOOTH
    state.sendspin_source = dict(
        connected=True, stream_requested=True, streaming=True,
        selected_source="bluetooth",
    )
    state.music_assistant = {"connected": True}
    flags = dict(publisher_present=True, stream_healthy=True,
                 console_playing=True, target_playing=True)
    source = Source.BLUETOOTH
    if missing in state.sendspin_source:
        state.sendspin_source[missing] = False
    elif missing in flags:
        flags[missing] = False
    elif missing == "generation":
        state.routing_session_generation = 1
    elif missing == "session_source":
        state.routing_session_source = Source.PHONO
    elif missing == "ma_connected":
        state.music_assistant["connected"] = False
    elif missing == "console_only":
        state.output_target = OutputTarget.CONSOLE
    elif missing == "no_source":
        source = Source.NONE
    assert distribution_is_ready(state, source, **flags) is (missing is None)


def test_local_loopback_command_uses_configured_audio_path() -> None:
    config = load_config(
        Path(__file__).parents[1] / "config" / "phono-console.example.toml"
    )
    command = local_loopback_command(config)
    assert command[0] == "alsaloop"
    assert command[command.index("-C") + 1] == "UFO202"
    assert command[command.index("-P") + 1] == "hw:CARD=vc4hdmi0,DEV=0"
    assert command[command.index("-r") + 1] == "48000"
    assert command[command.index("-t") + 1] == "40000"


def test_system_info_has_dashboard_network_shape() -> None:
    info = system_info()
    assert isinstance(info["hostname"], str)
    assert isinstance(info["addresses"], list)


def test_ma_loopback_profiling_does_not_change_audio_timing() -> None:
    config = load_config(
        Path(__file__).parents[1] / "config" / "phono-console.example.toml"
    )
    command = ma_loopback_command(config)
    assert "--xrun" in command
    assert command[command.index("-t") + 1] == "40000"
    assert command[command.index("-C") + 1] == "console_ma_capture"
    assert command[command.index("-P") + 1] == config.audio.playback_device


def test_distribution_restarts_when_group_stopped_but_source_request_is_stale() -> None:
    assert distribution_needs_start(
        stream_requested=True, console_playing=False
    )
    assert not distribution_needs_start(
        stream_requested=True, console_playing=True
    )
