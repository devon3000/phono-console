# Roadmap

## 1. UFO202 bench characterization

- Confirm ALSA capture and playback device names on the selected Pi.
- Confirm simultaneous full-duplex capture and playback.
- Build the lowest-latency stable software loopback.
- Measure round-trip latency; acceptance target is below 50 ms.
- Measure idle/noise levels to set phono detection thresholds.

## 2. Automatic router

- Implement audio-level detection with attack, release, and hysteresis.
- Implement UFO202 loopback lifecycle and pop-free transitions.
- Observe Music Assistant console-player state.
- Forward structured controller events to the MA-side integration logger.
- Enforce source priority and automatic resume behavior.
- Persist configuration, not transient playback state.

## 3. Whole-house vinyl

- Track production-ready Sendspin source-role client/provider support.
- Publish the UFO202 capture through the Sendspin source role.
- Start and stop the selected player group through the MA API.
- Ensure the console consumes the returned group stream.
- Verify synchronization across rooms.
- Do not build a temporary HTTP-radio/transcoding fallback.

## 4. Home Assistant and installation

- Expose mode, source activity, health, and whole-house control.
- Add safe service startup and recovery.
- Install the Pi, UFO202, and amplifier with adequate ventilation and properly
  enclosed mains wiring.
