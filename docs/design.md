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

## Feedback prevention

The phono capture must never ingest the returned Music Assistant output. The
UFO202 exposes distinct capture and playback endpoints over USB; the software
graph connects them only according to the active state. Whole-house mode sends
capture upstream while local playback consumes the returned stream.

