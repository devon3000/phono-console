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
