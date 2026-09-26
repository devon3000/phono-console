from pathlib import Path

from phono_console.config import load_config


def test_example_configuration_loads() -> None:
    path = Path(__file__).parents[1] / "config" / "phono-console.example.toml"
    config = load_config(path)
    assert config.audio.capture_device == "UFO202"
    assert config.audio.target_latency_ms == 40
    assert config.detection.needle_drop_peak_dbfs == -45.0
    assert config.routing.phono_mode_sticky_minutes == 60
    assert config.runtime.poll_interval_ms == 100
    assert config.runtime.api_host == "0.0.0.0"
    assert config.sendspin.server_url.endswith(":8927/sendspin")
    assert config.sendspin.source_enabled
    assert config.sendspin.state_dir == "/var/lib/phono-console/source"
    assert config.music_assistant.whole_house_players == ("Downstairs",)
    assert config.bluetooth.capture_device == "bluealsa"
    assert not config.bluetooth.enabled
    assert config.routing.distribution_target == "Downstairs"
    assert config.routing.local_fallback_enabled
    assert not config.controls.enabled
    assert config.controls.encoder_a_gpio == 17
    assert config.controls.encoder_b_gpio == 27
    assert config.controls.encoder_button_gpio == 22
    assert config.controls.volume_step == 2
