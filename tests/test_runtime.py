from pathlib import Path

from phono_console.config import load_config
import pytest

from phono_console.runtime import (
    local_loopback_command,
    system_info,
    validate_api_security,
)
from phono_console.runtime import run_daemon
from dataclasses import replace


def test_local_loopback_command_uses_configured_audio_path() -> None:
    config = load_config(
        Path(__file__).parents[1] / "config" / "phono-console.example.toml"
    )
    command = local_loopback_command(config)
    assert command[0] == "alsaloop"
    assert command[command.index("-C") + 1] == "UFO202"
    assert command[command.index("-P") + 1] == "UFO202"
    assert command[command.index("-r") + 1] == "48000"
    assert command[command.index("-t") + 1] == "40000"


def test_network_api_requires_token() -> None:
    with pytest.raises(RuntimeError, match="API_TOKEN"):
        validate_api_security("0.0.0.0", None)
    validate_api_security("0.0.0.0", "secret")
    validate_api_security("127.0.0.1", None)


def test_system_info_has_dashboard_network_shape() -> None:
    info = system_info()
    assert isinstance(info["hostname"], str)
    assert isinstance(info["addresses"], list)


def test_timestamped_backend_cannot_activate_before_device_probe() -> None:
    import asyncio

    config = load_config(
        Path(__file__).parents[1] / "config" / "phono-console.example.toml"
    )
    config = replace(
        config,
        audio_engine=replace(config.audio_engine, backend="timestamped"),
    )
    with pytest.raises(RuntimeError, match="timestamp probe passes"):
        asyncio.run(run_daemon(config))
