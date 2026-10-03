# Routing State Machine Specification

Status: implementation specification

## Purpose

Phono Console automatically selects the active physical source and lets the
user choose one output destination: the cabinet console or the synchronized
Downstairs Music Assistant group. This document defines one authoritative
routing model for the dashboard, encoder, Music Assistant, Sendspin, and local
audio processes.

The state machine controls routing. It does not change PCM processing, source
detection thresholds, Bluetooth pairing, CEC implementation, or the Sendspin
wire protocol.

## Invariants

1. There is exactly one persisted user routing intent: `output_target`, whose
   value is `console` or `downstairs`.
2. Dashboard, encoder, compatibility APIs, automatic expiry, and MA stop
   handling submit intents through the same coordinator.
3. Observations never silently rewrite user intent. An MA `source.stop` is an
   observation, not proof that the user selected Console.
4. A route reports `distributed_*` only after the source stream, console return
   player, and configured MA target all report active for the current session.
5. Each explicit output intent increments a generation number. Completion of
   work from an older generation cannot change current state.
6. Start and stop operations are serialized. A stop invalidates cached or
   in-flight start requests before asking MA to stop.
7. While Downstairs starts, physical sources continue locally. Failure to
   establish distribution is reported as degraded local fallback; it is never
   represented as successful Downstairs playback.
8. Phono has priority over Bluetooth, Bluetooth has priority over ordinary MA
   playback, and a transient phono peak may wake the amplifier only while no
   other source is active.
9. Only the route executor performs routing side effects. The reducer is pure.
10. The status API distinguishes requested output, transition phase, actual
    route, and confirmation details.

## Canonical state

### Desired state

```text
DesiredRoutingState
  output_target: console | downstairs
  generation: monotonically increasing integer
```

This replaces the independent `local_playback_only`, `phono_output_mode`, and
`whole_house_requested` control states. Compatibility fields may be returned
by the API temporarily, but they are derived and cannot be mutated separately.

### Observed state

```text
RoutingObservation
  active_source: none | phono | bluetooth
  ma_console_playing: bool
  ma_target_playing: bool
  ma_connected: bool
  sendspin_connected: bool
  sendspin_stream_requested: bool
  sendspin_streaming: bool
  sendspin_stream_healthy: bool
  distribution_capable: bool
```

Every observation belongs to the instant at which the reducer is called. A
Sendspin source session additionally carries the routing generation that
requested it.

### Machine state

```text
RoutingMachineState
  desired: DesiredRoutingState
  actual_route: idle | local_phono | local_bluetooth | ma_playback |
                distributed_phono | distributed_bluetooth
  phase: stable | starting_distribution | stopping_distribution | degraded
  session_source: none | phono | bluetooth
  session_generation: integer | null
  error: string | null
```

## Pure routing decision

The reducer returns a route and at most one distribution command.

| Active source | Requested output | Distribution confirmed | Decision |
| --- | --- | --- | --- |
| none | either | n/a | `ma_playback` when MA console is playing, otherwise `idle` |
| phono | console | n/a | `local_phono`, stop any distribution session |
| Bluetooth | console | n/a | `local_bluetooth`, stop any distribution session |
| phono | downstairs | yes | `distributed_phono` |
| Bluetooth | downstairs | yes | `distributed_bluetooth` |
| phono | downstairs | no, capable | `local_phono`, start distribution |
| Bluetooth | downstairs | no, capable | `local_bluetooth`, start distribution |
| physical source | downstairs | no, incapable | corresponding local route, `degraded` |

Source changes during a distributed session start a new generation-bound
session for the winning source. They never reuse the old source capture under
the same logical session.

## Confirmation rules

Distribution is confirmed only when all of the following are true:

- requested output is Downstairs;
- selected Sendspin source matches the active source;
- Sendspin is connected, streaming, requested, and its PCM feed is healthy;
- the Phono Console MA return player is playing;
- the configured MA distribution target is playing; and
- the active distribution session generation equals the current desired
  generation.

This is control-plane confirmation. MA may be unable to prove that every
physical group member is audible, so the UI must say “MA confirmed Downstairs”
rather than imply per-speaker acoustic verification.

## Transition executor

The executor owns an `asyncio.Lock` and a generation counter.

### Set Console

1. Increment generation and persist `console` atomically.
2. Disable source publication for the new generation.
3. Invalidate pending MA start requests.
4. Stop the configured target, best effort.
5. Reconcile the local route for the active source.
6. Commit `stable` after local output ownership is established. MA stop failure
   is reported but cannot prevent Console mode.

### Set Downstairs

1. Increment generation and persist `downstairs` atomically.
2. Enable source publication.
3. If a physical source is active, select it and submit one generation-bound
   MA start request.
4. Keep the corresponding local route until confirmation.
5. Commit the distributed route only after confirmation. On timeout or
   failure, retain local playback and report `degraded` without changing the
   requested output.

### Automatic source changes

The controller submits a source-change event to the same executor. If the
requested output is Downstairs, the executor supersedes the previous session
with a new generation-bound session. If requested output is Console, only the
local route changes.

### MA and Sendspin commands

- MA `source.start` may start capture only for the current enabled session.
- MA `source.stop` stops that capture session. It does not change
  `output_target`.
- A delayed command from a superseded session is ignored where the protocol
  exposes enough context; otherwise publication enablement and the current
  selected source provide the fail-safe guard.
- Bluetooth AVRCP pause is issued only for an explicit user stop, not for an
  internal MA bridge rebuild or routing handoff.

## Persistence and compatibility

- Persist one file named `output-target`, written by temporary-file replacement.
- On first startup after migration, `local-playback-only` maps to `console`;
  otherwise a fresh `phono-downstairs` marker maps to `downstairs`; then remove
  the legacy markers.
- `PUT /v1/output-mode` is authoritative.
- During one compatibility release:
  - `/v1/local-only` maps `true` to Console and `false` to Downstairs;
  - `/v1/phono-output` maps directly to the same output intent;
  - `/v1/whole-house` maps `true` to Downstairs and `false` to Console.
- Compatibility endpoints must not mutate independent booleans.

## Status contract

`GET /v1/status` exposes:

```json
{
  "routing": {
    "requested_output": "downstairs",
    "actual_route": "local_bluetooth",
    "phase": "starting_distribution",
    "generation": 42,
    "session_generation": 42,
    "session_source": "bluetooth",
    "confirmed": false,
    "error": null
  }
}
```

Legacy top-level fields are derived from `routing` while compatibility is
required.

## Timing and concurrency

- The detector loop must not await MA start/stop, CEC wake, or network
  reconnection. It publishes observations and applies already-decided local
  process changes.
- Network transitions run in executor tasks and publish completion events.
- A newer generation cancels or supersedes older transition work.
- Target start timeout is configurable. Timeout changes phase to `degraded`
  while retaining local audio.
- Recovery retries may use bounded backoff, but only one retry task exists for
  the current generation.

## Acceptance tests

1. Console/Downstairs changes from dashboard and encoder produce identical
   desired state and side effects.
2. Bluetooth continues locally until a Downstairs session is confirmed.
3. Phono continues locally until a Downstairs session is confirmed.
4. A delayed MA start completion after Console is selected cannot restart
   distribution.
5. A stop followed immediately by Downstairs submits a new play request rather
   than reusing a completed request.
6. MA source stop does not rewrite the requested output.
7. Activity from an unselected source cannot assert Sendspin line sense for the
   selected source.
8. Dashboard never labels a local fallback as active Downstairs.
9. MA or Sendspin failure leaves physical-source playback local and reports a
   degraded phase.
10. Restart restores only the single persisted output target.
11. All source-priority combinations are covered by a table-driven reducer
    test.
12. An assembled fake-MA/fake-Sendspin test covers rapid
    Downstairs → Console → Downstairs switching.

## Non-goals for this refactor

- Proving that every physical AirPlay/Sonos member is acoustically audible.
- Replacing Music Assistant grouping or AirPlay bridges.
- Moving MA return rendering into the native timestamped engine.
- Changing gain, limiter, Bluetooth buffering, or CEC protocol behavior.
