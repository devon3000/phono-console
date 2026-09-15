#!/usr/bin/env bash
set -u

# A disconnected phone is an idle state, not a service failure. Once BlueALSA
# exposes a capture transport, bridge it onto the fixed-rate internal bus with
# adaptive resampling to compensate for Bluetooth/ALSA clock drift.
while true; do
  if bluealsa-aplay -L 2>/dev/null | grep -q '^bluealsa:'; then
    alsaloop \
      -C bluealsa \
      -P console_bt_playback48 \
      -f S16_LE \
      -r 48000 \
      -c 2 \
      -t 200000 \
      -A sincfastest \
      -S samplerate || true
  fi
  sleep 2
done
