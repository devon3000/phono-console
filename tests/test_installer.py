import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_install_scripts_are_executable_and_valid_shell() -> None:
    for name in ("install.sh", "check-install.sh", "bluetooth-ingest.sh"):
        script = ROOT / "scripts" / name
        assert os.access(script, os.X_OK)
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_installer_secures_and_checks_the_network_api() -> None:
    installer = (ROOT / "scripts" / "install.sh").read_text()
    assert "sys.version_info < (3, 12)" in installer
    assert 'api_host = "0.0.0.0"' in installer
    assert "secrets.token_urlsafe(32)" in installer
    assert 'Authorization: Bearer $api_token' in installer
    assert "/health/live" in installer
    assert "healthy_count >= 3" in installer


def test_installer_offers_audio_devices_and_always_enables_services() -> None:
    installer = (ROOT / "scripts" / "install.sh").read_text()
    assert "list_hardware_devices arecord" in installer
    assert "list_hardware_devices aplay" in installer
    assert '"default|ALSA system default"' in installer
    assert '"null|virtual device' in installer
    assert (
        "systemctl enable phono-console.service phono-console-player.service"
        in installer
    )
    assert (
        "systemctl restart phono-console.service phono-console-player.service"
        in installer
    )
    assert "systemctl disable phono-console.service" not in installer
    assert 'User=phono-console' in (
        ROOT / "systemd" / "phono-console.service"
    ).read_text()
    assert 'Restart=always' in (
        ROOT / "systemd" / "phono-console-player.service"
    ).read_text()
    assert "bluez-alsa-utils" in installer
    assert "snd-aloop" in installer
    assert 'PHONO_PLAYER_AUDIO_DEVICE="console_ma_playback"' in installer
    assert "type dmix" not in installer
    assert (ROOT / "systemd" / "phono-console-bluetooth.service").is_file()
    bluetooth_unit = (
        ROOT / "systemd" / "phono-console-bluetooth.service"
    ).read_text()
    assert "/opt/phono-console/current/bin/bluetooth-ingest" in bluetooth_unit
    assert "StartLimitBurst" not in bluetooth_unit
    ingest = (ROOT / "scripts" / "bluetooth-ingest.sh").read_text()
    assert "-A sincfastest" in ingest
    assert "-S samplerate" in ingest
    assert "console_bt_playback48" in ingest


def test_installer_migrates_the_legacy_null_capture() -> None:
    installer = (ROOT / "scripts" / "install.sh").read_text()
    assert 'capture_device" == "null"' in installer
    assert "Replacing legacy null capture" in installer
    assert 'capture_device = "phono_capture"' in installer
    assert '"$playback_device" == "phono_direct"' in installer


def test_dashboard_assets_are_declared_as_package_data() -> None:
    project = (ROOT / "pyproject.toml").read_text()
    assert '[tool.setuptools.package-data]' in project
    for asset in ("dashboard.html", "dashboard.css", "dashboard.js"):
        assert (ROOT / "src" / "phono_console" / "static" / asset).is_file()
