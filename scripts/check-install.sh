#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="${1:-/etc/phono-console/config.toml}"

echo "phono-console: $(command -v phono-console || echo MISSING)"
echo "arecord:       $(command -v arecord || echo MISSING)"
echo "aplay:         $(command -v aplay || echo MISSING)"
echo "alsaloop:      $(command -v alsaloop || echo MISSING)"
echo "sendspin:      $(command -v sendspin || echo MISSING)"
echo "config:        $CONFIG_FILE"

if [[ ! -r "$CONFIG_FILE" ]]; then
  echo "Configuration is not readable." >&2
  exit 1
fi

phono-console --config "$CONFIG_FILE"
phono-console diagnose
systemctl is-enabled phono-console.service
systemctl is-active phono-console.service
systemctl is-enabled phono-console-player.service
systemctl is-active phono-console-player.service

ENV_FILE="/etc/phono-console/environment"
api_token="$(awk -F= '$1 == "PHONO_CONSOLE_API_TOKEN" {
  print substr($0, index($0, "=") + 1)
}' "$ENV_FILE" | tail -1)"
if [[ -z "$api_token" ]]; then
  echo "PHONO_CONSOLE_API_TOKEN is missing." >&2
  exit 1
fi
curl --fail --silent --show-error \
  -H "Authorization: Bearer $api_token" \
  http://127.0.0.1:8765/health
echo

echo
echo "Base installation looks good. Run the live input check with:"
echo "  phono-console levels --config $CONFIG_FILE"
