# Phono Console

Software-defined audio routing for a turntable console built around one
Behringer UFO202 and one Raspberry Pi.

## Physical signal path

```text
Turntable → UFO202 phono input → USB → Raspberry Pi
                                      ↓
Amplifier ← UFO202 RCA output  ← USB playback
    ↓
Console speakers
```

Every source passes through the Pi. There is no split analog feed, separate
phono amplifier input, hardware monitor path, or physical input selector.

## Automatic behavior

- Phono input becomes active: play it locally through a low-latency software
  loopback.
- Music Assistant starts playing to the console: play the MA stream instead.
- Whole-house vinyl is requested: publish the phono capture to Music Assistant
  and play the returned MA stream locally, keeping the console synchronized
  with the other rooms.
- Music Assistant stops: resume local vinyl if phono input is still active.
- Nothing is active: output silence.

Music Assistant has priority over local phono. Level thresholds, debounce, and
hold times prevent record noise or brief pauses from causing rapid switching.

## Current status

This repository starts with the routing contract and a hardware-independent
policy engine. UFO202/ALSA integration and Music Assistant API integration are
the next implementation steps. Target measured local round-trip latency is
under 50 ms.

See [docs/design.md](docs/design.md) and [docs/roadmap.md](docs/roadmap.md).

