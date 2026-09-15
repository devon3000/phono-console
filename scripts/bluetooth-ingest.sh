#!/usr/bin/env bash
set -u

# A disconnected Bluetooth source is normal. Wait for BlueALSA to expose a
# transport, run the bridge while it exists, then return to waiting when the
# phone disconnects. Keeping this loop in one service avoids systemd's start
# limiter turning an idle receiver into a failed appliance component.
while true; do
  if bluealsa-aplay -L 2>/dev/null | grep -q '^bluealsa:'; then
    alsaloop \
      -C bluealsa \
      -P console_bt_playback \
      -f S16_LE \
      -r 48000 \
      -c 2 \
      -t 40000 || true
  fi
  sleep 2
done
