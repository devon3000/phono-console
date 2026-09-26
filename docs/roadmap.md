# Roadmap

## 1. UFO202 bench characterization

- Confirm ALSA capture and playback device names on the selected Pi.
- Confirm simultaneous full-duplex capture and playback.
- Build the lowest-latency stable software loopback.
- Measure round-trip latency; acceptance target is below 50 ms.
- Measure idle/noise levels to set phono detection thresholds.

Use `phono-console diagnose --config /etc/phono-console/config.toml` for the
device/tool report and one-second configured-PCM probe. The implemented
`ArecordLevelMonitor` reads 48 kHz, 16-bit PCM, reports RMS dBFS, times out
stalled reads, and reopens the capture path with bounded exponential backoff.

## 2. Automatic router

- Implement audio-level detection with attack, release, and hysteresis.
- Implement UFO202 loopback lifecycle and pop-free transitions.
- Observe Music Assistant console-player state.
- Maintain the authenticated MA websocket and reconnect after connection loss.
- Forward structured controller events to the MA-side integration logger.
- Enforce source priority and automatic resume behavior.
- Persist configuration, not transient playback state.
- Reconcile the active audio route continuously and restart a dead loopback.
- Fail silent immediately when capture disappears; retain MA output ownership
  when playback telemetry drops mid-stream.

## 3. Whole-house vinyl

- Track production-ready Sendspin source-role client/provider support.
  Status (September 2026): available in Music Assistant versions that support
  the encrypted aiosendspin 9.x source protocol after installing the
  `sendspin_source` plugin (fixed 48 kHz / 16-bit / stereo output, paired
  connections required, streaming starts only on a server `start` command),
  and aiosendspin 9.1.1 provides the client API
  (`SendspinClient.create_source_capture()` with `start`/`feed`/`stop`).
  Constraint: the `sendspin` player package pins `aiosendspin~=6.0.1`, so the
  installer gives the player its own venv while the application venv carries
  aiosendspin 9.x for the in-process source client.
- Publish the UFO202 capture through the Sendspin source role. Implemented:
  the daemon's `SendspinSourcePublisher` streams the shared capture when the
  MA `sendspin_source` plugin commands it and reports line-sense signal state.
- Start and stop the selected player group through the MA API. Implemented via
  `PUT /v1/whole-house` against `music_assistant.whole_house_players`;
  MA/Home-Assistant-initiated playback needs no daemon involvement.
- Bench-test pairing from the Music Assistant UI against the headless
  auto-accepted pairing window.
- Ensure the console consumes the returned group stream.
- Verify synchronization across rooms.
- Do not build a temporary HTTP-radio/transcoding fallback.

### Timestamp-preserving source pipeline

Detailed design, staging, test criteria, and rollback are maintained in
[Timestamped audio engine implementation plan](timestamped-audio-engine-plan.md).

- Implemented as an opt-in timestamp-aware native capture/fan-out process.
- ALSA timestamps and first-sample capture time are carried with PCM supplied
  to Sendspin.
- Local adaptive playback, activity detection, metering, and distribution use
  one authoritative capture timeline.
- Discontinuities and bounded queue loss are explicit in engine telemetry.
- The legacy graph remains available for rollback during the device soak.
- Remaining: move MA-return rendering into the engine so one component owns
  every physical-output route.
- Bench-test uninterrupted Bluetooth distribution and cross-room sync for at
  least one hour, including pause/resume, phone reconnect, MA reconnect, and
  network interruption.


## 4. Home Assistant and installation

- Expose mode, source activity, health, and whole-house control.
- Maintain a rerunnable Raspberry Pi installer that detects the UFO202, creates
  configuration and a protected environment file, and verifies dependencies.
- Preserve configuration by default, atomically activate validated releases,
  roll executables back after failed startup, and run services unprivileged.
- Expose separate liveness/readiness health plus per-component failures,
  configured devices, version, uptime, and error details on the dashboard.
- Bench-test service startup, recovery, and USB reconnect behavior on the Pi.
- Install the Pi, UFO202, and amplifier with adequate ventilation and properly
  enclosed mains wiring.

## 5. Cabinet controls

- The volume API and route-dependent volume ownership are implemented.
- Mechanical layout and control semantics are documented in
  [Cabinet hardware controls](hardware-controls.md).
- GPIO17/GPIO27/GPIO22 are assigned to encoder A/B/switch; the common return is
  ground.
- A disabled-by-default GPIO worker with quadrature decoding, debounce, rapid
  detent coalescing, route-aware volume, and push-switch control is implemented.
- Bench-test direction and bounce behavior with the physical PEC11H; set the
  configuration's `reverse` flag if necessary.
- Finalize the straight-line three-LED PCB only after checking a full-size
  drilling template against the false drawer.
