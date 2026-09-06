# Phono Console

Software-defined audio routing for a turntable console built around one
Behringer UFO202 and one Raspberry Pi.

## Raspberry Pi installation

On Raspberry Pi OS, clone the repository and run the interactive installer:

```bash
git clone https://github.com/devon3000/phono-console.git
cd phono-console
sudo ./scripts/install.sh
```

It installs ALSA and the Python application into an isolated virtual
environment, detects a connected UFO202-compatible USB audio device, asks only
for audio and Music Assistant settings, writes `/etc/phono-console/config.toml`,
creates a separate root-only token file, validates the configuration, and runs
the hardware diagnostic. It is safe to rerun and backs up an existing config.

After adding the Music Assistant token to `/etc/phono-console/environment`,
verify the installation with:

```bash
sudo ./scripts/check-install.sh
```

The automatic routing daemon and its systemd unit are deliberately not enabled
by this installer yet: the runtime composition and native Sendspin source role
remain unfinished. The installed diagnostic, configuration validation, and
live input calibration commands are usable now.

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

## Input calibration

Run the live UFO202 input meter with the same configuration used by the
turntable service:

```bash
phono-console levels --config /etc/phono-console.toml
```

The terminal shows independent left/right peak and short-window RMS levels in
dBFS, maximum peaks since startup, and persistent per-channel clipping flags.
Press Ctrl-C after playing a representative loud passage or full record side;
the command prints a final calibration summary.

Meter calculations observe the captured 16-bit stereo PCM and never alter,
normalize, or resample it. The capture monitor also retains its latest stereo
reading and session maxima, so the running service can consume those readings
from its existing capture stream without opening the UFO202 a second time.
When invoked as a standalone command, `levels` owns the ALSA capture device and
should be run while the service is stopped unless the ALSA device supports
sharing.

The embedded control API is designed for Home Assistant and defaults to
loopback-only access. With `PHONO_CONSOLE_API_TOKEN` set, requests use a bearer
token. Its initial endpoints are:

- `GET /health`
- `GET /v1/status`
- `PUT /v1/whole-house` with `{\"enabled\": true|false}`

Whole-house capture deliberately depends on native Sendspin source-role support.
The project will not add a temporary HTTP-radio or transcoding workaround while
that support matures.

See [docs/design.md](docs/design.md) and [docs/roadmap.md](docs/roadmap.md).
