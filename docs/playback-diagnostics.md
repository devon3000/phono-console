# Cabinet MA-return playback diagnostics

These diagnostics observe the separate Sendspin player and the ALSA loopback
that moves its returned audio to the cabinet's HDMI output. They do not change
the 180 ms calibrated Sendspin delay, buffering, rate correction, or routing.
They do not measure Sonos acoustic output and cannot alone prove inter-room sync.

## Player audio log

The player service now invokes the same installed Sendspin console entrypoint
through a standard-library wrapper. Normal INFO/warnings remain in the journal.
Only `sendspin.audio` and `sendspin.audio_connector` get DEBUG logging into:

`/var/lib/phono-console/sendspin/playback-diagnostics.log`

The log rotates at 1 MiB with two backups (`.1` and `.2`), about 3 MiB total.
Available statistics depend on the installed player version; these can include
sync error, queued audio duration, speed correction, sample insertions/drops,
underflows, gaps/overlaps, and reanchors. Raw protocol logging is not enabled.
The full application status response is not stored because it contains pairing
credentials.

Writes are queued on a dedicated logging thread; the bounded 256-record queue
drops diagnostic records instead of blocking audio if storage stalls.
`diagnostic_drops` in each log line identifies lost diagnostic records, not lost
audio. Unavailable log storage must not prevent the player from starting.

## Router/ALSA observations

Once per second a worker thread reads existing `/proc/asound/.../status` and
`hw_params` files. It never opens an ALSA PCM or writes a mixer/device control.
Only handles belonging to the MA loopback process or Sendspin player are included.
Command-line contents are used only for process identification, never recorded.

`ma_return_timing` journal events record:

- actual/requested route and routing generation;
- MA loopback PID and liveness;
- observed PCM state, owner, trigger timestamp, hardware/application pointers;
- rate, buffer/period size, and delay in milliseconds when the kernel provides it;
- reader scheduling lateness (not a CPU-utilization measurement).

Events occur on observed handle/state changes, buffer delay jumps of at least
100 ms, pointer decreases, or scheduling lateness of at least 100 ms. Otherwise
there is a ten-second heartbeat. Latest observations are available under
`components.ma_return_diagnostics` in `/v1/status`.

Pointer decreases can be wraps rather than resets. PCM observations are
sequential, not an atomic snapshot. Some kernels omit delay/pointer fields or
deny access; missing observations are reported as unavailable, not inferred to
be an underrun. A one-second poll may miss a shorter underrun, so MA-return
`alsaloop --xrun` profiling additionally records incident-specific buffer timing
in the normal journal. No automatic recovery action is triggered by diagnostics.

## After the next audible loss of sync

Keep playback running and note the time and which room is ahead. On the Pi:

```bash
sudo journalctl -u phono-console -u phono-console-player \
  -u phono-console-audio-engine --since "15 minutes ago" \
  --no-pager -o short-precise > /tmp/pi-sync.log
sudo tail -200 /var/lib/phono-console/sendspin/playback-diagnostics.log
```

Preserve the current log and rotated backups if the event was earlier; do not
reinstall or restart before capture. Also collect MA's server log for the same
time range. Installation itself restarts the services and resets their timing,
so a recovered snapshot cannot reconstruct a pre-restart buffer offset.
