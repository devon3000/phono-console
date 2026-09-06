# Roadmap

## 1. UFO202 bench characterization

- Confirm ALSA capture and playback device names on the selected Pi.
- Confirm simultaneous full-duplex capture and playback.
- Build the lowest-latency stable software loopback.
- Measure round-trip latency; acceptance target is below 50 ms.
- Measure idle/noise levels to set phono detection thresholds.

Use `phono-console diagnose` for the initial device/tool report. The implemented
`ArecordLevelMonitor` reads 48 kHz, 16-bit PCM, reports RMS dBFS, and reopens the
capture path after failure.

## 2. Automatic router

- Implement audio-level detection with attack, release, and hysteresis.
- Implement UFO202 loopback lifecycle and pop-free transitions.
- Observe Music Assistant console-player state.
- Maintain the authenticated MA websocket and reconnect after connection loss.
- Forward structured controller events to the MA-side integration logger.
- Enforce source priority and automatic resume behavior.
- Persist configuration, not transient playback state.

## 3. Whole-house vinyl

- Track production-ready Sendspin source-role client/provider support.
  Status (September 2026): available. Music Assistant stable 2.10.2 ships the
  `sendspin_source` plugin (fixed 48 kHz / 16-bit / stereo output, paired
  connections required, streaming starts only on a server `start` command),
  and aiosendspin 9.1.1 provides the client API
  (`SendspinClient.create_source_capture()` with `start`/`feed`/`stop`).
  Constraint: the `sendspin` player package pins `aiosendspin~=6.0.1`, so the
  source client cannot share the application venv with the player until the
  player tracks aiosendspin 9.x; the player's separate systemd unit makes a
  dedicated player venv the likely resolution.
- Publish the UFO202 capture through the Sendspin source role.
- Start and stop the selected player group through the MA API.
- Ensure the console consumes the returned group stream.
- Verify synchronization across rooms.
- Do not build a temporary HTTP-radio/transcoding fallback.

## 4. Home Assistant and installation

- Expose mode, source activity, health, and whole-house control.
- Maintain a rerunnable Raspberry Pi installer that detects the UFO202, creates
  configuration and a protected environment file, and verifies dependencies.
- Bench-test service startup, recovery, and USB reconnect behavior on the Pi.
- Install the Pi, UFO202, and amplifier with adequate ventilation and properly
  enclosed mains wiring.
