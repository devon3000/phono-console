import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_install_scripts_are_executable_and_valid_shell() -> None:
    for name in ("install.sh", "check-install.sh"):
        script = ROOT / "scripts" / name
        assert os.access(script, os.X_OK)
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_installer_secures_and_checks_the_network_api() -> None:
    installer = (ROOT / "scripts" / "install.sh").read_text()
    assert "sys.version_info < (3, 12)" in installer
    assert 'api_host = "0.0.0.0"' in installer
    assert "secrets.token_urlsafe(32)" in installer
    assert 'Authorization: Bearer $api_token' in installer
    assert "/health" in installer


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


def test_dashboard_assets_are_declared_as_package_data() -> None:
    project = (ROOT / "pyproject.toml").read_text()
    assert '[tool.setuptools.package-data]' in project
    for asset in ("dashboard.html", "dashboard.css", "dashboard.js"):
        assert (ROOT / "src" / "phono_console" / "static" / asset).is_file()
