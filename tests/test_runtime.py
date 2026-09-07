from pathlib import Path

from phono_console.config import load_config
import pytest

from phono_console.runtime import local_loopback_command, validate_api_security


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
