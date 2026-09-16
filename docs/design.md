# Design

## Invariants

1. The amplifier is permanently connected to the UFO202 RCA output.
2. The turntable is permanently connected to the UFO202 phono input.
3. The Pi is the only router; source changes are signal-driven and require no
   physical or dashboard source switch.
4. Phono defaults to direct local playback. Its user-selected output mode is
   session-sticky across short silence and record changes, then expires to local.
5. The optional synchronized phono mode and normal Bluetooth mode return to
   the console through Music Assistant so the Downstairs group stays aligned.
6. Local vinyl uses a short software loopback and targets less than 50 ms
   round-trip latency after signal detection.

## States

| State | UFO capture | Local output | Music Assistant publication |
| --- | --- | --- | --- |
| `idle` | Monitored | Silence | Off |
| `local_phono` | Monitored | Minimum-latency default | Disabled |
| `local_bluetooth` | Monitored | Low-latency fallback | Unavailable |
| `ma_playback` | Monitored | MA stream | Off |
| `distributed_phono` | Active | Returned MA stream | Phono capture |
| `distributed_bluetooth` | Active | Returned MA stream | Bluetooth capture |

## Music Assistant transport decision

The console is a Sendspin player for synchronized Music Assistant playback.
The automatically selected phono/Bluetooth source uses Sendspin's source role
to expose `Console Input` as a native Music Assistant audio source. The daemon runs an in-process source
client (aiosendspin) with a persistent identity and pairing store; the Music
Assistant `sendspin_source` plugin commands when streaming starts and stops.
The controller plays Bluetooth on `Downstairs` automatically. Phono is direct
local by default and moves to the same synchronized path only when the user
selects **Synchronized Downstairs**. That preference is output routing, not
manual source selection.

## Automatic policy

Inputs to the policy engine:

- `phono_active`: derived from the UFO202 capture level using an adjustable
  threshold, attack time, and release/hold time.
- `ma_playing`: derived from Music Assistant player state for the console.
- `bluetooth_active`: decoded Bluetooth PCM above threshold; connection alone
  is not activity.
- `phono_output_mode`: persistent `local` or `downstairs` user preference.
- `phono_mode_sticky_minutes`: inactivity before Downstairs expires to local
  (60 minutes by default).
- distribution health: the Sendspin source, MA API, target, and return path.

Priority, highest first: active phono, actively streaming Bluetooth, Music
Assistant playback, idle. Phono's default local route is deliberate; its
Downstairs mode and Bluetooth distribution target the fixed `Downstairs` group.
When a requested distributed path is unavailable, playback falls back locally
without changing the stored preference.

Every change reevaluates current activity. Recovery from local fallback waits
for stable distribution health before returning to `Downstairs`.

## Operational events

The controller emits structured events for route changes, device availability,
audio-process failures, Music Assistant connectivity, and recovery. The MA-side
vinyl provider logs its own stream lifecycle in Music Assistant. The Pi adapter
forwards controller events through the integration channel supported by that
provider; it does not depend on an undocumented arbitrary server-log endpoint.

## Local control and status

The daemon maintains a bounded event history and current status snapshot. A
small authenticated HTTP API exposes health, status, and time-limited Bluetooth
pairing to Home Assistant. The installer binds it to the LAN,
generates a bearer token, verifies the authenticated health endpoint, and the
daemon refuses network exposure if that token is absent.

## Feedback prevention

The phono capture must never ingest the returned Music Assistant output. The
UFO202 exposes distinct capture and playback endpoints over USB; the software
graph connects them only according to the active state. Normal distribution
sends capture upstream while local playback consumes the returned stream.

The shared dsnoop capture lets the level monitor, the local loopback, and the
Sendspin source client read the UFO202 simultaneously without contending for
the hardware. The routing decision governs local output only: the loopback
runs solely in `local_phono`. The source client streams capture upstream only
while the Music Assistant provider commands it, and Music Assistant playback
is produced by the separate Sendspin player service, so the returned stream
never re-enters the capture path.

## Known Bluetooth timestamp limitation

The implementation sequence and acceptance gates are defined in
[Timestamped audio engine implementation plan](timestamped-audio-engine-plan.md).

The current Bluetooth path is:

```text
A2DP/RTP -> BlueALSA -> FFmpeg resampler -> ALSA loopback
         -> arecord raw PCM -> Sendspin source
```

Bluetooth packets arrive with transport timestamps, but those timestamps do
not survive the decoded ALSA PCM interface or `arecord`'s raw stdout format.
The Sendspin publisher therefore cannot pass the original timestamps through.
It currently anchors the first PCM sample to the Sendspin client's monotonic
clock and advances subsequent timestamps from captured frame count. This is a
stabilization measure: it prevents buffered blocks sent in a burst from being
assigned overlapping send-time timestamps, but it assumes a continuous sample
clock and cannot faithfully represent a real capture gap, clock correction,
underrun, overrun, or transport restart.

This is an architectural limitation for synchronized distribution even when
the same path sounds acceptable through direct local playback. The durable
design is a single timestamp-aware Bluetooth capture/fan-out component that:

- exclusively owns the BlueALSA PCM capture;
- reads ALSA hardware timestamps instead of piping unannotated raw PCM through
  `arecord`;
- performs adaptive resampling while maintaining one authoritative sample
  timeline and measured pipeline latency;
- preserves and reports discontinuities rather than hiding them;
- feeds activity detection, direct local playback, and Sendspin from the same
  timestamped frame stream; and
- removes the Bluetooth ALSA-loopback/`dsnoop` multi-reader fan-out.

Passing the original A2DP/RTP timestamps end to end would require a BlueALSA
integration that exposes them before or alongside PCM decode. If that is not
practical, ALSA hardware timestamps are the required source of truth at the
capture boundary. The generalized source pipeline should offer the same
timestamp contract to phono capture, even though phono has no upstream RTP
clock.
