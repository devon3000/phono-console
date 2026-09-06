#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/phono-console"
CONFIG_DIR="/etc/phono-console"
CONFIG_FILE="$CONFIG_DIR/config.toml"
ENV_FILE="$CONFIG_DIR/environment"
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer with sudo: sudo ./scripts/install.sh" >&2
  exit 1
fi

if [[ ! -f "$SOURCE_DIR/pyproject.toml" ]]; then
  echo "Run the installer from a phono-console checkout." >&2
  exit 1
fi

prompt() {
  local label="$1" default="$2" result
  read -r -p "$label [$default]: " result </dev/tty
  printf '%s' "${result:-$default}"
}

toml_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

detect_ufo_card() {
  local line
  line="$(arecord -l 2>/dev/null | awk '
    BEGIN { IGNORECASE=1 }
    /^card [0-9]+:/ && ($0 ~ /UFO202|UCA202|USB Audio CODEC/) {
      sub(/^card /, ""); sub(/:.*/, ""); print; exit
    }')"
  printf '%s' "$line"
}

echo "Installing system packages..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  alsa-utils python3 python3-venv

echo "Installing phono-console into $APP_DIR..."
install -d -m 0755 "$APP_DIR" "$CONFIG_DIR"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install "$SOURCE_DIR"
ln -sfn "$APP_DIR/venv/bin/phono-console" /usr/local/bin/phono-console

card="$(detect_ufo_card)"
if [[ -n "$card" ]]; then
  echo "Detected UFO202-compatible USB audio card $card."
  default_device="plughw:CARD=$card,DEV=0"
else
  echo "UFO202 not detected. You can rerun this installer after connecting it."
  default_device="UFO202"
fi

capture_device="$(prompt "ALSA capture device" "$default_device")"
playback_device="$(prompt "ALSA playback device" "$default_device")"
ma_url="$(prompt "Music Assistant URL" "http://music-assistant.local")"
ma_player="$(prompt "Music Assistant console player" "Phono Console")"
vinyl_source="$(prompt "Music Assistant vinyl source" "Console Vinyl")"
sendspin_url="$(prompt "Sendspin server URL" "ws://music-assistant.local:8927/sendspin")"

if [[ -e "$CONFIG_FILE" ]]; then
  backup="$CONFIG_FILE.$(date -u +%Y%m%dT%H%M%SZ).bak"
  cp -a "$CONFIG_FILE" "$backup"
  echo "Backed up the existing configuration to $backup"
fi

cat >"$CONFIG_FILE" <<EOF
[audio]
capture_device = "$(toml_escape "$capture_device")"
playback_device = "$(toml_escape "$playback_device")"
target_latency_ms = 40
sample_rate = 48000
channels = 2
detection_window_ms = 100

[detection]
phono_threshold_dbfs = -48.0
attack_ms = 250
release_ms = 5000
hysteresis_db = 6.0

[music_assistant]
base_url = "$(toml_escape "$ma_url")"
console_player = "$(toml_escape "$ma_player")"
vinyl_source = "$(toml_escape "$vinyl_source")"
token_env = "PHONO_CONSOLE_MA_TOKEN"

[sendspin]
server_url = "$(toml_escape "$sendspin_url")"
player_name = "$(toml_escape "$ma_player")"
source_name = "$(toml_escape "$vinyl_source")"
source_enabled = false

[runtime]
poll_interval_ms = 100
api_host = "127.0.0.1"
api_port = 8765
api_token_env = "PHONO_CONSOLE_API_TOKEN"
EOF
chmod 0640 "$CONFIG_FILE"

if [[ ! -e "$ENV_FILE" ]]; then
  cat >"$ENV_FILE" <<'EOF'
# Add tokens after the equals signs. Keep this file root-readable only.
PHONO_CONSOLE_MA_TOKEN=
PHONO_CONSOLE_API_TOKEN=
EOF
  chmod 0600 "$ENV_FILE"
fi

echo
echo "Validating configuration..."
phono-console --config "$CONFIG_FILE"
echo
echo "Probing audio hardware (a missing UFO202 is okay before installation day)..."
phono-console diagnose || true
echo
echo "Setup complete."
echo "Configuration: $CONFIG_FILE"
echo "Secrets:       $ENV_FILE"
echo "Input meter:   phono-console levels --config $CONFIG_FILE"
echo
echo "Rerun this installer after connecting the UFO202 to auto-detect its ALSA device."
