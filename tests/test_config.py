from pathlib import Path

from phono_console.config import load_config


def test_example_configuration_loads() -> None:
    path = Path(__file__).parents[1] / "config" / "phono-console.example.toml"
    config = load_config(path)
    assert config.audio.capture_device == "UFO202"
    assert config.audio.target_latency_ms == 40
    assert config.runtime.poll_interval_ms == 100
    assert config.runtime.api_host == "0.0.0.0"
    assert config.sendspin.server_url.endswith(":8927/sendspin")
    assert config.sendspin.source_enabled
    assert config.sendspin.state_dir == "/var/lib/phono-console/source"
    assert config.music_assistant.whole_house_players == ("Downstairs",)
