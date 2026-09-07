#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/phono-console"
CONFIG_DIR="/etc/phono-console"
CONFIG_FILE="$CONFIG_DIR/config.toml"
ENV_FILE="$CONFIG_DIR/environment"
PLAYER_ENV_FILE="$CONFIG_DIR/player.env"
SERVICE_FILE="/etc/systemd/system/phono-console.service"
PLAYER_SERVICE_FILE="/etc/systemd/system/phono-console-player.service"
ALSA_FILE="/etc/alsa/conf.d/99-phono-console.conf"
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
      gsub(/:/, "", $3); print $3; exit
    }')"
  printf '%s' "$line"
}

echo "Installing system packages..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  alsa-utils curl libportaudio2 python3 python3-venv

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
  echo "phono-console requires Python 3.12 or newer." >&2
  echo "Install a current Raspberry Pi OS release, then rerun this installer." >&2
  exit 1
fi

echo "Installing phono-console into $APP_DIR..."
install -d -m 0755 "$APP_DIR" "$CONFIG_DIR"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install "$SOURCE_DIR"
ln -sfn "$APP_DIR/venv/bin/phono-console" /usr/local/bin/phono-console

# The sendspin player pins aiosendspin 6.x while the routing daemon's source
# client needs 9.x, so the player lives in its own venv.
python3 -m venv "$APP_DIR/player-venv"
"$APP_DIR/player-venv/bin/pip" install --upgrade pip
"$APP_DIR/player-venv/bin/pip" install "sendspin>=7.5,<8"
ln -sfn "$APP_DIR/player-venv/bin/sendspin" /usr/local/bin/sendspin

card="$(detect_ufo_card)"
if [[ -n "$card" ]]; then
  echo "Detected UFO202-compatible USB audio card $card."
  default_device="plughw:CARD=$card,DEV=0"
else
  echo "UFO202 not detected. You can rerun this installer after connecting it."
  default_device="UFO202"
fi

# Share one hardware capture between the activity meter and local loopback.
if [[ -n "$card" ]]; then
  install -d -m 0755 /etc/alsa/conf.d
  cat >"$ALSA_FILE" <<EOF
pcm.phono_capture {
  type dsnoop
  ipc_key 24680
  slave {
    pcm "hw:$card,0"
    rate 48000
    channels 2
  }
}

pcm.phono_playback {
  type dmix
  ipc_key 24681
  slave {
    pcm "hw:$card,0"
    rate 48000
    channels 2
  }
}
EOF
  default_device="phono_capture"
  default_playback_device="phono_playback"
else
  default_playback_device="$default_device"
fi

capture_device="$(prompt "ALSA capture device" "$default_device")"
playback_device="$(prompt "ALSA playback device" "$default_playback_device")"
ma_url="$(prompt "Music Assistant URL" "http://music-assistant.local")"
ma_player="$(prompt "Music Assistant console player" "Phono Console")"
vinyl_source="$(prompt "Music Assistant vinyl source" "Console Vinyl")"
whole_house_group="$(prompt "Whole-house player group" "Downstairs")"
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
whole_house_players = ["$(toml_escape "$whole_house_group")"]

[sendspin]
server_url = "$(toml_escape "$sendspin_url")"
player_name = "$(toml_escape "$ma_player")"
source_name = "$(toml_escape "$vinyl_source")"
source_enabled = true
state_dir = "/var/lib/phono-console/source"

[runtime]
poll_interval_ms = 100
api_host = "0.0.0.0"
api_port = 8765
api_token_env = "PHONO_CONSOLE_API_TOKEN"
EOF
chmod 0640 "$CONFIG_FILE"

# The Sendspin player unit reads these values; regenerated on every run like
# the main configuration.
cat >"$PLAYER_ENV_FILE" <<EOF
PHONO_PLAYER_URL="$(toml_escape "$sendspin_url")"
PHONO_PLAYER_NAME="$(toml_escape "$ma_player")"
PHONO_PLAYER_AUDIO_DEVICE="$(toml_escape "$playback_device")"
EOF
chmod 0644 "$PLAYER_ENV_FILE"

if [[ ! -e "$ENV_FILE" ]]; then
  cat >"$ENV_FILE" <<'EOF'
# Add the Music Assistant token if the server requires one.
PHONO_CONSOLE_MA_TOKEN=
PHONO_CONSOLE_API_TOKEN=
EOF
fi
chmod 0600 "$ENV_FILE"

api_token="$(awk -F= '$1 == "PHONO_CONSOLE_API_TOKEN" {
  print substr($0, index($0, "=") + 1)
}' "$ENV_FILE" | tail -1)"
if [[ -z "$api_token" ]]; then
  api_token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  if grep -q '^PHONO_CONSOLE_API_TOKEN=' "$ENV_FILE"; then
    sed -i "s|^PHONO_CONSOLE_API_TOKEN=.*$|PHONO_CONSOLE_API_TOKEN=$api_token|" \
      "$ENV_FILE"
  else
    printf 'PHONO_CONSOLE_API_TOKEN=%s\n' "$api_token" >>"$ENV_FILE"
  fi
  echo "Generated a Home Assistant API token in $ENV_FILE"
fi

install -m 0644 "$SOURCE_DIR/systemd/phono-console.service" "$SERVICE_FILE"
install -m 0644 "$SOURCE_DIR/systemd/phono-console-player.service" "$PLAYER_SERVICE_FILE"
systemctl daemon-reload

echo
echo "Validating configuration..."
phono-console --config "$CONFIG_FILE"
echo
echo "Probing audio hardware (a missing UFO202 is okay before installation day)..."
phono-console diagnose || true
if [[ -n "$card" ]]; then
  systemctl enable --now phono-console.service phono-console-player.service
  healthy=false
  for _attempt in $(seq 1 30); do
    if curl --fail --silent --show-error \
      -H "Authorization: Bearer $api_token" \
      "http://127.0.0.1:8765/health" >/dev/null; then
      healthy=true
      break
    fi
    sleep 1
  done
  if [[ "$healthy" != true ]]; then
    systemctl status phono-console.service --no-pager || true
    echo "The router was installed but did not become healthy." >&2
    echo "Inspect logs with: journalctl -u phono-console -n 100" >&2
    exit 1
  fi
  echo "Router status:  systemctl status phono-console --no-pager"
  echo "Player status:  systemctl status phono-console-player --no-pager"
  echo "Live logs:      journalctl -u phono-console -u phono-console-player -f"
else
  systemctl disable phono-console.service phono-console-player.service \
    >/dev/null 2>&1 || true
  echo "Services installed but not enabled because the UFO202 was not detected."
fi
echo
echo "Setup complete."
echo "Configuration: $CONFIG_FILE"
echo "Secrets:       $ENV_FILE"
echo "Input meter:   phono-console levels --config $CONFIG_FILE"
echo "Home Assistant: see $SOURCE_DIR/home-assistant/README.md"
echo
echo "Rerun this installer after connecting the UFO202 to auto-detect its ALSA device."
