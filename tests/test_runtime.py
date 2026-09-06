from pathlib import Path

from phono_console.config import load_config
from phono_console.runtime import local_loopback_command


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
