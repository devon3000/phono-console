#!/usr/bin/env bash
set -u

# A disconnected phone is an idle state, not a service failure. Once BlueALSA
# exposes a capture transport, bridge it onto the fixed-rate internal bus with
# FFmpeg's asynchronous resampler. alsaloop's libsamplerate synchronization is
# unstable on Raspberry Pi and produced repeated output underruns in practice.
while true; do
  pcm_listing="$(bluealsa-aplay -L 2>/dev/null)"
  if grep -q '^bluealsa:' <<<"$pcm_listing"; then
    input_rate="$(awk '
      / channels [0-9]+ Hz$/ {
        for (i = 2; i <= NF; i++) if ($i == "Hz") { print $(i - 1); exit }
      }
    ' <<<"$pcm_listing")"
    input_rate="${input_rate:-48000}"
    ffmpeg \
      -hide_banner \
      -nostdin \
      -loglevel warning \
      -f alsa \
      -sample_rate "$input_rate" \
      -ac 2 \
      -i bluealsa \
      -af "aresample=48000:async=1000" \
      -ar 48000 \
      -ac 2 \
      -f alsa \
      console_bt_playback48 || true
  fi
  sleep 2
done
