#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="${1:-/etc/phono-console/config.toml}"

echo "phono-console: $(command -v phono-console || echo MISSING)"
echo "arecord:       $(command -v arecord || echo MISSING)"
echo "aplay:         $(command -v aplay || echo MISSING)"
echo "alsaloop:      $(command -v alsaloop || echo MISSING)"
echo "sendspin:      $(command -v sendspin || echo MISSING)"
echo "bluetoothctl:  $(command -v bluetoothctl || echo MISSING)"
echo "bluealsa:      $(command -v bluealsa || command -v bluealsad || echo MISSING)"
echo "audio engine:  $(readlink -f /opt/phono-console/current/phono-audio-engine 2>/dev/null || echo MISSING)"
echo "config:        $CONFIG_FILE"

if [[ ! -r "$CONFIG_FILE" ]]; then
  echo "Configuration is not readable." >&2
  exit 1
fi

phono-console --config "$CONFIG_FILE"
if ! phono-console diagnose --config "$CONFIG_FILE"; then
  echo "Audio probe failed; the dashboard will show the failed component." >&2
fi
systemctl is-enabled phono-console.service
systemctl is-active phono-console.service
systemctl is-enabled phono-console-player.service
systemctl is-active phono-console-player.service
audio_engine_backend="$(python3 - "$CONFIG_FILE" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as handle:
    print(tomllib.load(handle).get("audio_engine", {}).get("backend", "legacy"))
PY
)"
controls_enabled="$(python3 - "$CONFIG_FILE" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as handle:
    print(str(tomllib.load(handle).get("controls", {}).get("enabled", False)).lower())
PY
)"
if [[ "$controls_enabled" == "true" ]]; then
  if ! compgen -G '/dev/gpiochip*' >/dev/null; then
    echo "Hardware controls are enabled but no GPIO character device exists." >&2
    exit 1
  fi
  if ! id -nG phono-console | tr ' ' '\n' | grep -qx gpio; then
    echo "The phono-console user is not in the gpio group." >&2
    exit 1
  fi
  echo "hardware controls: enabled"
fi
if [[ "$audio_engine_backend" == "timestamped" ]]; then
  systemctl is-enabled phono-console-audio-engine.service
  systemctl is-active phono-console-audio-engine.service
  if systemctl is-enabled phono-console-bluetooth.service >/dev/null 2>&1; then
    echo "Legacy Bluetooth ingest must be disabled in timestamped mode." >&2
    exit 1
  fi
else
  systemctl is-enabled phono-console-bluetooth.service
  systemctl is-active phono-console-bluetooth.service || \
    echo "Bluetooth ingest is waiting for an A2DP source."
fi

curl --fail --silent --show-error \
  http://127.0.0.1:8765/health/live
echo

if ! curl --fail --silent --show-error \
  http://127.0.0.1:8765/health/ready; then
  echo >&2
  echo "Dashboard is live, but the audio path is not ready. Check it for details." >&2
fi
echo

echo
echo "Base installation and dashboard are reachable. Run the live input check with:"
echo "  phono-console levels --config $CONFIG_FILE"
