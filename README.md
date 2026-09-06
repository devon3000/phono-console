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
  using Sendspin source support and play the returned MA stream locally,
  keeping the console synchronized with the other rooms.
- Music Assistant stops: resume local vinyl if phono input is still active.
- Nothing is active: output silence.

Music Assistant has priority over local phono. Level thresholds, debounce, and
hold times prevent record noise or brief pauses from causing rapid switching.

## Current status

The controller now includes configuration loading, phono activity detection,
source-priority decisions, supervised routing processes, a real `arecord` level
monitor, PCM RMS metering, and Music Assistant websocket state tracking. Target
measured local round-trip latency is under 50 ms.

Run the non-mutating target probe on the Raspberry Pi with:

```bash
phono-console diagnose
```

It reports ALSA capture/playback devices and availability of the loopback and
Sendspin client commands.

Whole-house capture deliberately depends on native Sendspin source-role support.
The project will not add a temporary HTTP-radio or transcoding workaround while
that support matures.

See [docs/design.md](docs/design.md) and [docs/roadmap.md](docs/roadmap.md).
