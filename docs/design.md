# Design

## Invariants

1. The amplifier is permanently connected to the UFO202 RCA output.
2. The turntable is permanently connected to the UFO202 phono input.
3. The Pi is the only router; source changes require no physical audio switch.
4. Whole-house vinyl returns to the console through Music Assistant so all
   rooms experience the same buffering and remain synchronized.
5. Local vinyl uses a short software loopback and targets less than 50 ms
   round-trip latency.

## States

| State | UFO capture | Local output | Music Assistant publication |
| --- | --- | --- | --- |
| `idle` | Monitored | Silence | Off |
| `local_phono` | Monitored | Low-latency phono loopback | Off |
| `ma_playback` | Monitored | MA stream | Off |
| `whole_house_phono` | Active | Returned MA stream | Phono capture |

## Music Assistant transport decision

The console is a Sendspin player for synchronized Music Assistant playback.
Whole-house vinyl uses Sendspin's source role to expose the UFO202 capture as a
native Music Assistant audio source. Source publication remains disabled until
the compatible Sendspin source client and MA provider are available and pass
bench testing.

This is an intentional dependency, not a temporary gap to bridge with an HTTP
radio stream, FIFO transcoder, or unsynchronized local monitor. Waiting keeps
one timing model for the console and every other room.

## Automatic policy

Inputs to the policy engine:

- `phono_active`: derived from the UFO202 capture level using an adjustable
  threshold, attack time, and release/hold time.
- `ma_playing`: derived from Music Assistant player state for the console.
- `whole_house_requested`: an explicit Home Assistant or UI request. Audio
  activity cannot infer whether the user wants vinyl in other rooms.

Priority, highest first:

1. Whole-house phono request
2. Music Assistant playback
3. Active phono input
4. Idle

When MA playback ends, the controller reevaluates current phono activity and
returns to local vinyl automatically when appropriate.

## Operational events

The controller emits structured events for route changes, device availability,
audio-process failures, Music Assistant connectivity, and recovery. The MA-side
vinyl provider logs its own stream lifecycle in Music Assistant. The Pi adapter
forwards controller events through the integration channel supported by that
provider; it does not depend on an undocumented arbitrary server-log endpoint.

## Feedback prevention

The phono capture must never ingest the returned Music Assistant output. The
UFO202 exposes distinct capture and playback endpoints over USB; the software
graph connects them only according to the active state. Whole-house mode sends
capture upstream while local playback consumes the returned stream.

The runtime owns two mutually exclusive capture-consuming processes: the local
loopback and the Sendspin source client. Route transitions stop the old
consumer before starting the new one. Music Assistant playback is produced by
the MA player client and therefore requires neither capture process.
