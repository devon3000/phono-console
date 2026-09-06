# Phono Console

Raspberry Pi control software for integrating a turntable, a vintage console's
speakers, and Music Assistant without creating an out-of-sync local speaker.

## Operating modes

| Mode | Console speakers | Whole house | Signal path |
| --- | --- | --- | --- |
| `local_vinyl` | Turntable | Off | Phono preamp → console amplifier |
| `broadcast_vinyl` | Music Assistant stream | Turntable | Phono preamp → UFO202 → Pi → Music Assistant |
| `music_assistant` | Music Assistant stream | Music Assistant | Music Assistant → Pi → console amplifier |

In `broadcast_vinyl`, the console listens to the same distributed digital stream
as every other room. This deliberately avoids mixing a zero-latency analog path
with a delayed network path.

## Intended hardware

- Turntable and phono preamp
- Behringer UFO202 USB audio interface for vinyl capture
- Raspberry Pi (final model not yet locked)
- Audio output from the Pi to the new console amplifier
- A controllable input switch or relay for choosing direct phono vs Pi audio
- The console's existing speakers, driven by the replacement amplifier

See [docs/architecture.md](docs/architecture.md) for the signal design and
[docs/roadmap.md](docs/roadmap.md) for the build sequence.

## Development

The first code is a hardware-independent mode controller. It can be exercised
without GPIO or audio hardware:

```bash
python -m phono_console.cli --config config/phono-console.example.toml status
python -m phono_console.cli --config config/phono-console.example.toml set local_vinyl
```

The controller currently logs the requested transition. GPIO switching, audio
capture, Music Assistant control, and Home Assistant integration are explicit
next steps rather than simulated functionality.

## Safety

This project controls only low-voltage switching. Do not put mains voltage on a
Pi GPIO board or solderless breadboard. Any mains switching for amplifier power
must use a properly enclosed, rated device.

