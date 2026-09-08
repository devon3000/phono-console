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
config_backup=""

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

config_value() {
  python3 - "$CONFIG_FILE" "$1" "$2" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as handle:
    value = tomllib.load(handle)[sys.argv[2]][sys.argv[3]]
if isinstance(value, list):
    print(value[0] if value else "")
else:
    print(value)
PY
}

list_hardware_devices() {
  local command="$1"
  "$command" -l 2>/dev/null | sed -nE \
    's/^card ([0-9]+): ([^ ]+) \[([^]]+)\], device ([0-9]+): (.*)$/hw:CARD=\2,DEV=\4|card \1: \3, device \4: \5/p'
}

preferred_device() {
  local entry
  for entry in "$@"; do
    if [[ "$entry" =~ UFO202|UCA202|USB.Audio.CODEC ]]; then
      printf '%s' "${entry%%|*}"
      return
    fi
  done
  if (( $# > 0 )); then
    printf '%s' "${1%%|*}"
  else
    # ALSA's null PCM supplies silence for capture and discards playback. This
    # keeps the API/dashboard and network clients usable before hardware arrives.
    printf 'null'
  fi
}

choose_audio_device() {
  local direction="$1" default="$2" choice index entry
  shift 2
  local -a options=("$@")
  options+=(
    "default|ALSA system default"
    "null|virtual device (silence for capture; discard playback)"
  )

  echo >&2
  echo "Available ALSA $direction devices:" >&2
  for index in "${!options[@]}"; do
    entry="${options[$index]}"
    printf '  %d) %-30s %s\n' \
      "$((index + 1))" "${entry%%|*}" "${entry#*|}" >&2
  done
  echo "You may also enter any ALSA PCM name directly." >&2
  choice="$(prompt "ALSA $direction device (number or name)" "$default")"
  if [[ "$choice" =~ ^[0-9]+$ ]] && \
      (( choice >= 1 && choice <= ${#options[@]} )); then
    printf '%s' "${options[$((choice - 1))]%%|*}"
  else
    printf '%s' "$choice"
  fi
}

echo "Installing system packages..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  alsa-utils curl libportaudio2 python3 python3-venv

if ! getent group phono-console >/dev/null; then
  groupadd --system phono-console
fi
if ! id phono-console >/dev/null 2>&1; then
  useradd --system --gid phono-console --groups audio \
    --home-dir /var/lib/phono-console --shell /usr/sbin/nologin phono-console
else
  usermod -a -G audio phono-console
fi
install -d -o phono-console -g phono-console -m 0750 /var/lib/phono-console
chown -R phono-console:phono-console /var/lib/phono-console

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
  echo "phono-console requires Python 3.12 or newer." >&2
  echo "Install a current Raspberry Pi OS release, then rerun this installer." >&2
  exit 1
fi

echo "Installing phono-console into $APP_DIR..."
install -d -m 0755 "$APP_DIR" "$APP_DIR/releases" "$CONFIG_DIR"
release_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
release_dir="$APP_DIR/releases/$release_id"
install -d -m 0755 "$release_dir"
python3 -m venv "$release_dir/venv"
"$release_dir/venv/bin/pip" install --upgrade pip
"$release_dir/venv/bin/pip" install "$SOURCE_DIR"

# The sendspin player pins aiosendspin 6.x while the routing daemon's source
# client needs 9.x, so the player lives in its own venv.
python3 -m venv "$release_dir/player-venv"
"$release_dir/player-venv/bin/pip" install --upgrade pip
"$release_dir/player-venv/bin/pip" install "sendspin>=7.5,<8"

keep_existing=false
if [[ -e "$CONFIG_FILE" ]]; then
  keep_answer="$(prompt "Keep existing configuration and audio selection? (y/n)" "y")"
  if [[ "$keep_answer" =~ ^[Yy] ]]; then
    keep_existing=true
  fi
fi

if [[ "$keep_existing" == true ]]; then
  echo "Keeping existing configuration. Choose 'n' on a future run to reconfigure."
  capture_device="$(config_value audio capture_device)"
  playback_device="$(config_value audio playback_device)"
  raw_capture_device="$capture_device"
  raw_playback_device="$playback_device"
  ma_url="$(config_value music_assistant base_url)"
  ma_player="$(config_value music_assistant console_player)"
  vinyl_source="$(config_value music_assistant vinyl_source)"
  whole_house_group="$(config_value music_assistant whole_house_players)"
  sendspin_url="$(config_value sendspin server_url)"
else
mapfile -t capture_hardware < <(list_hardware_devices arecord)
mapfile -t playback_hardware < <(list_hardware_devices aplay)
default_capture_device="$(preferred_device "${capture_hardware[@]}")"
default_playback_device="$(preferred_device "${playback_hardware[@]}")"

if [[ "$default_capture_device" == *UFO202* || \
      "$default_capture_device" == *UCA202* || \
      "$default_capture_device" == *CODEC* ]]; then
  echo "Detected a UFO202-compatible capture device."
else
  echo "UFO202 not detected; another device or the virtual null device may be used."
fi

raw_capture_device="$(choose_audio_device \
  "capture" "$default_capture_device" "${capture_hardware[@]}")"
raw_playback_device="$(choose_audio_device \
  "playback" "$default_playback_device" "${playback_hardware[@]}")"

# Hardware PCMs get shared wrappers so the level monitor, local loopback, and
# Sendspin source can coexist. Named/virtual PCMs are used directly because
# their sharing behavior belongs to their own ALSA definition.
install -d -m 0755 /etc/alsa/conf.d
: >"$ALSA_FILE"
capture_device="$raw_capture_device"
if [[ "$raw_capture_device" == hw:* ]]; then
  cat >>"$ALSA_FILE" <<EOF
pcm.phono_capture {
  type dsnoop
  ipc_key 24680
  slave {
    pcm "$raw_capture_device"
    rate 48000
    channels 2
  }
}
EOF
  capture_device="phono_capture"
fi

playback_device="$raw_playback_device"
if [[ "$raw_playback_device" == hw:* ]]; then
  cat >>"$ALSA_FILE" <<EOF
pcm.phono_playback {
  type dmix
  ipc_key 24681
  slave {
    pcm "$raw_playback_device"
    rate 48000
    channels 2
  }
}
EOF
  playback_device="phono_playback"
fi
ma_url="$(prompt "Music Assistant URL" "http://music-assistant.local")"
ma_player="$(prompt "Music Assistant console player" "Phono Console")"
vinyl_source="$(prompt "Music Assistant vinyl source" "Console Vinyl")"
whole_house_group="$(prompt "Whole-house player group" "Downstairs")"
sendspin_url="$(prompt "Sendspin server URL" "ws://music-assistant.local:8927/sendspin")"

if [[ -e "$CONFIG_FILE" ]]; then
  config_backup="$CONFIG_FILE.$(date -u +%Y%m%dT%H%M%SZ).bak"
  cp -a "$CONFIG_FILE" "$config_backup"
  echo "Backed up the existing configuration to $config_backup"
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
fi
chown root:phono-console "$CONFIG_FILE"
chmod 0640 "$CONFIG_FILE"

# The Sendspin player unit reads values mirrored from the preserved or newly
# generated main configuration.
cat >"$PLAYER_ENV_FILE" <<EOF
PHONO_PLAYER_URL="$(toml_escape "$sendspin_url")"
PHONO_PLAYER_NAME="$(toml_escape "$ma_player")"
PHONO_PLAYER_AUDIO_DEVICE="$(toml_escape "$playback_device")"
EOF
chown root:phono-console "$PLAYER_ENV_FILE"
chmod 0640 "$PLAYER_ENV_FILE"

if [[ ! -e "$ENV_FILE" ]]; then
  cat >"$ENV_FILE" <<'EOF'
# Add the Music Assistant token if the server requires one.
PHONO_CONSOLE_MA_TOKEN=
PHONO_CONSOLE_API_TOKEN=
EOF
fi
chown root:phono-console "$ENV_FILE"
chmod 0640 "$ENV_FILE"

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

echo
echo "Validating configuration..."
"$release_dir/venv/bin/phono-console" --config "$CONFIG_FILE"
echo
echo "Probing audio hardware (services remain available when hardware is absent)..."
"$release_dir/venv/bin/phono-console" diagnose --config "$CONFIG_FILE" || true

# Publish the fully installed and validated release in one rename. A failed
# install leaves the currently running release and symlink untouched.
previous_release="$(readlink -f "$APP_DIR/current" 2>/dev/null || true)"
candidate_link="$APP_DIR/current.$release_id"
ln -s "$release_dir" "$candidate_link"
mv -Tf "$candidate_link" "$APP_DIR/current"
ln -sfn "$APP_DIR/current/venv/bin/phono-console" /usr/local/bin/phono-console
ln -sfn "$APP_DIR/current/player-venv/bin/sendspin" /usr/local/bin/sendspin

install -m 0644 "$SOURCE_DIR/systemd/phono-console.service" "$SERVICE_FILE"
install -m 0644 "$SOURCE_DIR/systemd/phono-console-player.service" "$PLAYER_SERVICE_FILE"
systemctl daemon-reload
systemctl enable phono-console.service phono-console-player.service
systemctl restart phono-console.service phono-console-player.service
healthy=false
healthy_count=0
for _attempt in $(seq 1 30); do
  if curl --fail --silent --show-error \
    -H "Authorization: Bearer $api_token" \
    "http://127.0.0.1:8765/health/live" >/dev/null; then
    healthy_count=$((healthy_count + 1))
    if (( healthy_count >= 3 )); then
      healthy=true
      break
    fi
  else
    healthy_count=0
  fi
  sleep 1
done
if [[ "$healthy" != true ]]; then
  systemctl status phono-console.service --no-pager || true
  if [[ -n "$config_backup" && -f "$config_backup" ]]; then
    cp -a "$config_backup" "$CONFIG_FILE"
    echo "Restored configuration from $config_backup" >&2
  fi
  if [[ -n "$previous_release" && -d "$previous_release" ]]; then
    rollback_link="$APP_DIR/rollback.$release_id"
    ln -s "$previous_release" "$rollback_link"
    mv -Tf "$rollback_link" "$APP_DIR/current"
    echo "Rolled back executables to $previous_release" >&2
    systemctl restart phono-console.service phono-console-player.service || true
  fi
  echo "Services are enabled, but the dashboard did not become healthy." >&2
  echo "Inspect logs with: journalctl -u phono-console -n 100" >&2
  exit 1
fi
echo "Router status:  systemctl status phono-console --no-pager"
echo "Player status:  systemctl status phono-console-player --no-pager"
echo "Live logs:      journalctl -u phono-console -u phono-console-player -f"
echo
echo "Setup complete."
echo "Configuration: $CONFIG_FILE"
echo "Secrets:       $ENV_FILE"
echo "Input meter:   phono-console levels --config $CONFIG_FILE"
echo "Capture PCM:   $capture_device (selected $raw_capture_device)"
echo "Playback PCM:  $playback_device (selected $raw_playback_device)"
dashboard_address="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "Dashboard:     http://${dashboard_address:-PHONO_CONSOLE_IP}:8765/"
echo "Home Assistant: see $SOURCE_DIR/home-assistant/README.md"
echo
echo "Rerun this installer whenever audio hardware changes to select new devices."
