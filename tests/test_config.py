from pathlib import Path

import pytest

from phono_console.config import load_config


def write_amplifier_config(path, settings):
    example = Path(__file__).parents[1] / "config" / "phono-console.example.toml"
    text = example.read_text()
    before, rest = text.split("[amplifier]", 1)
    _, after = rest.split("[controls]", 1)
    path.write_text(before + "[amplifier]\n" + settings + "\n[controls]" + after)


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
    assert config.sendspin.source_timeline_lead_ms == 3000
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


def test_missing_amplifier_bounds_use_measured_curve(tmp_path):
    path = tmp_path / "config.toml"
    write_amplifier_config(path, 'enabled = true')
    amp = load_config(path).amplifier
    assert (amp.volume_min, amp.volume_mid, amp.volume_max, amp.volume_mid_position) == (12, 20, 40, 60)


def test_explicit_legacy_bounds_remain_linear(tmp_path):
    path = tmp_path / "config.toml"
    write_amplifier_config(path, 'volume_min = 15\nvolume_max = 45')
    amp = load_config(path).amplifier
    assert amp.volume_mid is None
    assert (amp.volume_min, amp.volume_max) == (15, 45)


@pytest.mark.parametrize("settings", [
    'volume_mid = 12', 'volume_mid = 40', 'volume_mid_position = 1',
    'volume_mid_position = 100',
])
def test_invalid_amplifier_calibration_is_rejected(tmp_path, settings):
    path = tmp_path / "config.toml"
    write_amplifier_config(path, settings)
    with pytest.raises(ValueError, match="midpoint"):
        load_config(path)
