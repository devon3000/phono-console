#!/usr/bin/env bash
set -euo pipefail

volume="${!#}"
source /etc/phono-console/environment
curl --fail --silent --show-error \
  --request PUT \
  --header "Authorization: Bearer $PHONO_CONSOLE_API_TOKEN" \
  --header "Content-Type: application/json" \
  --header "X-Phono-Volume-Source: music_assistant" \
  --data "{\"volume\":$volume}" \
  http://127.0.0.1:8765/v1/amplifier/volume >/dev/null
