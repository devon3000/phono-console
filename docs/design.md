# Design

## Invariants

1. The amplifier is permanently connected to the UFO202 RCA output.
2. The turntable is permanently connected to the UFO202 phono input.
3. The Pi is the only router; source changes are signal-driven and require no
   physical or dashboard source switch.
4. Phono and Bluetooth return to the console through Music Assistant so all
   rooms experience the same buffering and remain synchronized.
5. Local vinyl uses a short software loopback and targets less than 50 ms
   round-trip latency.

## States

| State | UFO capture | Local output | Music Assistant publication |
| --- | --- | --- | --- |
| `idle` | Monitored | Silence | Off |
| `local_phono` | Monitored | Low-latency fallback | Unavailable |
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
The controller plays that source on `Downstairs` automatically. Direct local
rendering is reserved for failure of the MA/Sendspin/network path.

This is an intentional dependency, not a temporary gap to bridge with an HTTP
radio stream, FIFO transcoder, or unsynchronized local monitor. Waiting keeps
one timing model for the console and every other room.

## Automatic policy

Inputs to the policy engine:

- `phono_active`: derived from the UFO202 capture level using an adjustable
  threshold, attack time, and release/hold time.
- `ma_playing`: derived from Music Assistant player state for the console.
- `bluetooth_active`: decoded Bluetooth PCM above threshold; connection alone
  is not activity.
- distribution health: the Sendspin source, MA API, target, and return path.

Priority, highest first: active phono, actively streaming Bluetooth, Music
Assistant playback, idle. Distribution normally targets the fixed `Downstairs`
group. Local phono/Bluetooth playback is a health-driven fallback only.

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
