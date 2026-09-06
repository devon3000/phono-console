# Roadmap

## Phase 1 — Bench validation

- Confirm the UFO202 enumerates on the chosen Pi.
- Measure capture-monitoring latency and noise floor.
- Establish a stable line-level output from the Pi.
- Verify Music Assistant can ingest and distribute the capture stream.
- Confirm the console can join that same stream in sync.

## Phase 2 — Switching

- Choose an isolated, low-voltage-controlled stereo input switch.
- Implement GPIO selection with safe startup defaults.
- Add transition ordering to prevent pops and feedback loops.
- Persist and restore the requested operating mode.

## Phase 3 — Home Assistant

- Expose the three modes as a select entity.
- Publish current mode, capture health, and audio-device availability.
- Add automations for console playback and whole-house vinyl.

## Phase 4 — Cabinet installation

- Mount the Pi, UFO202, switch, and replacement amplifier.
- Provide ventilation, strain relief, and serviceable connectors.
- Keep mains wiring enclosed and physically separate from low-voltage audio and GPIO.

