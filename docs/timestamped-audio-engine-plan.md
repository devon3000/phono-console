# Timestamped audio engine implementation plan

## Implementation status

Work is isolated on `feature/timestamped-audio-engine`.

- Phase 0: legacy default and rollback path preserved.
- Phase 1: native ALSA timestamp probe implemented and installed with releases;
  awaiting execution against the UFO202 and BlueALSA PCMs on the target Pi.
- Phase 2: versioned C/Python frame protocol, bounded Python fan-out, and exact
  timestamp forwarding into Sendspin implemented with automated tests.
- Timestamped backend activation remains intentionally blocked until the Phase
  1 device gate passes. Selecting it cannot silently run the legacy graph.

## Purpose

Replace the Bluetooth `BlueALSA -> FFmpeg -> ALSA loopback -> arecord` path and
the phono `dsnoop -> arecord/alsaloop` fan-out with one timestamp-aware audio
engine. The engine must preserve capture timing, prevent competing ALSA
consumers, and provide the same selected PCM frames to local playback,
metering/activity detection, and the Sendspin source.

This plan addresses the observed distributed-Bluetooth symptoms: periodic
dropouts, route churn, bursty audio, and varispeed. Frame-count-derived
Sendspin timestamps remain an interim mitigation and are not the target design.

## Decision

Implement a small C17 executable linked with `libasound` and `libsamplerate`.
Keep routing policy, Bluetooth control, Music Assistant integration, Sendspin,
HTTP/API state, and the dashboard in Python.

C is selected because ALSA's timestamp, status, pointer, poll-descriptor, and
recovery APIs are native C interfaces. A standalone executable also isolates
audio real-time concerns from the Python event loop without introducing a
second language toolchain. The Raspberry Pi installer will build the pinned
repository source; no downloaded binary is required.

The engine is the only process allowed to open capture or physical playback
PCMs. Existing FFmpeg, `arecord`, and `alsaloop` processes are disabled when
the new engine feature flag is enabled.

## Verified platform assumptions and validation gate

ALSA exposes the high-resolution timestamp and corresponding available-frame
position through `snd_pcm_htimestamp()`. Timestamping must be enabled in the
PCM software parameters. BlueALSA exposes Bluetooth capture as an ALSA PCM and
also reports transport delay, native rate, channels, codec, and running state.

These APIs exist, but the usefulness and clock domain of `snd_pcm_htimestamp()`
on the BlueALSA plugin must be verified on the actual Pi. Phase 1 is therefore
a release gate, not optional investigation.

The probe must establish for both `phono_capture` and `bluealsa`:

- timestamp clock type and monotonicity;
- timestamp-to-frame-position behavior across at least 10 minutes;
- native format/rate and negotiated period/buffer sizes;
- timestamp delta versus frames read;
- reported ALSA and BlueALSA delay;
- behavior on pause, resume, disconnect, reconnect, overrun, and service
  restart; and
- whether BlueALSA timestamps advance from the Bluetooth transport or merely
  reflect application read time.

Proceed with the design below if timestamp error remains bounded and tracks
frame delivery. If BlueALSA only supplies read-time timestamps, implement its
D-Bus PCM transport reader or extend BlueALSA at the pre-ALSA boundary rather
than manufacturing continuity downstream.

## Audio graph

```text
                         +-----------------------------+
UFO202 capture ----------> Phono ALSA capture adapter  |
                         |                             |
BlueALSA capture --------> Bluetooth capture adapter   |-- timestamped frames
                         |                             |          |
MA loopback capture -----> MA return capture adapter   |          v
                         +-----------------------------+   Python IPC client
                                      |                       |       |
                                      v                       |       +-> Sendspin
                              priority/output route            +-> meters/activity
                                      |
                                      v
                              UFO202 ALSA playback
```

The engine always monitors phono and any connected Bluetooth transport.
Python continues to decide `phono > Bluetooth > MA`. It sends the chosen local
output route to the engine. For distributed phono/Bluetooth, the engine renders
the MA return locally while Python publishes the selected capture frames to
Sendspin.

## Timestamped frame contract

Every capture adapter emits a common logical record:

```text
AudioFrame
  protocol_version: u16
  source: phono | bluetooth | ma_return
  flags: discontinuity | xrun_recovered | clock_reset | end_of_stream
  sequence: u64
  first_sample_time_us: i64       # CLOCK_MONOTONIC_RAW domain
  source_rate_hz: u32
  output_rate_hz: u32             # normally 48000
  channels: u16                   # normally 2
  frames: u16                     # normally 960 / 20 ms
  reported_transport_delay_us: i32 # telemetry; never applied to timestamp
  pcm: interleaved signed 16-bit little-endian
```

Rules:

1. `first_sample_time_us` identifies the first output-rate sample in `pcm`.
2. Timestamp and sequence never silently jump. A restart, xrun, clock reset, or
   discarded interval sets a discontinuity flag and begins a new epoch.
3. Nominal sample-rate conversion does not change the capture timeline.
4. Bluetooth transport/FIFO delay is reported separately; it is not hidden by
   shifting or stretching PCM.
5. A bounded queue may drop a whole oldest frame, but the following frame must
   carry `discontinuity` and a drop counter must increase.
6. Sendspin receives the frame timestamp converted into the client clock
   domain. It never substitutes websocket send time.
7. A sample timestamp is a coordinate on the source sample timeline, not the
   time at which software happened to read, transform, enqueue, or transmit
   that sample. Processing duration, queue residence, and network send time
   must never be added to or subtracted from it.
8. Resampling maps output sample positions back to input sample positions. Its
   filter execution time does not alter timestamps. Any filter phase/group
   delay that changes which source instant an output sample represents is
   handled by the deterministic sample-position mapping, not a stopwatch.

There is no requirement for civil/wall-clock time. Sendspin does require the
sample coordinate to be expressed in its synchronized monotonic time domain,
so the capture adapter must establish a mapping between device sample position
and that monotonic domain. For A2DP this ideally preserves the RTP sample
counter plus a measured clock-domain anchor. For ALSA capture it uses the
hardware frame position paired with `snd_pcm_htimestamp()`. Once established,
the mapping advances from sample positions—not callback or processing times.

Use a versioned little-endian binary protocol over an `AF_UNIX SOCK_SEQPACKET`
socket at `/run/phono-console/audio-engine.sock`. One packet contains one
header and one PCM block, preserving frame boundaries. The socket is owned by
`phono-console:phono-console` with mode `0660`. Control messages use the same
socket and include request IDs and explicit acknowledgements.

## Clock and resampling model

There are three independent clocks: Bluetooth transport, UFO202 USB, and the
Music Assistant/Sendspin time domain. They must not be controlled by two
simultaneous adaptive resamplers.

- Capture adapters use ALSA timestamps and frame positions to timestamp native
  samples.
- Native-to-48 kHz conversion uses `libsamplerate` and preserves the source
  timeline through an explicit input-position/output-position mapping.
- The Sendspin sink forwards capture timestamps. Sendspin is responsible for
  synchronized network rendering.
- Local Bluetooth playback uses a separate adaptive ratio controlled only by
  the physical playback buffer occupancy. This local correction is never fed
  back into the distributed stream.
- Phono capture and playback are expected to share the UFO202 USB clock, but
  buffer control still detects and reports divergence rather than assuming it.
- The MA return path is paced by its capture PCM and rendered to the physical
  output with one buffer controller inside the engine.

Initial correction limits must be conservative and observable. Start with a
maximum soft correction of 250 ppm. A larger required correction is treated as
a clock discontinuity or configuration fault, not audible varispeed.

## Native engine components

### ALSA capture adapter

- Open one PCM with `SND_PCM_NONBLOCK` and poll descriptors.
- Negotiate and then report the actual format, rate, channels, access mode,
  period, and buffer size; fail rather than silently accepting mono or an
  unsupported format.
- Enable monotonic high-resolution timestamps.
- At each read, combine `snd_pcm_htimestamp()`'s timestamp and available-frame
  position with the number of frames consumed to calculate first-sample time.
- Recover `-EPIPE`, `-ESTRPIPE`, and device removal explicitly and begin a new
  timestamp epoch after recovery.
- Never busy-loop on an idle or disconnected BlueALSA PCM.

### Resampler and frame assembler

- Convert native input to 48 kHz, stereo, S16_LE.
- Preserve fractional resampler state across blocks.
- Emit fixed 20 ms frames for Sendspin and metering.
- Flush/reset only on a declared discontinuity.
- Measure queue occupancy, correction ratio, and accumulated input/output
  frames. Processing-duration metrics may be recorded only as operational
  evidence of CPU starvation or backpressure; they are not audio timing inputs
  and must not modify frame timestamps.

### Activity and meters

The engine computes per-frame peak and RMS for each source and sends compact
telemetry to Python. Python retains attack, release, hysteresis, priority, and
session-maximum policy. This removes extra ALSA readers while keeping policy
configuration and dashboard behavior in the existing application.

### Physical output sink

- Exclusively own `phono_direct`/the raw UFO202 playback PCM.
- Accept route commands: `silence`, `local_phono`, `local_bluetooth`, and
  `ma_return`.
- Apply a short configurable fade-out/fade-in at route boundaries.
- Prebuffer a bounded amount before starting a new source.
- Write silence during idle so output ownership does not churn.
- Recover underruns without reopening unless the device was removed.
- Acknowledge a route only after the output transition has completed.

## Python changes

### New modules

- `audio_engine_protocol.py`: binary frame/control codec and version checking.
- `audio_engine_client.py`: supervised Unix-socket connection, bounded per-
  source queues, health counters, and route commands.
- `engine_level_monitor.py`: adapts engine telemetry to the existing
  `LevelMonitor` interface.
- `engine_source_stream.py`: yields timestamped selected-source frames to the
  Sendspin publisher.

### Sendspin source

Change the PCM stream contract from `AsyncIterator[bytes]` to
`AsyncIterator[TimestampedPcm]`. Pass every `first_sample_time_us` to
`SourceCapture.feed()`. On a discontinuity, stop and restart the Sendspin
capture epoch unless the aiosendspin API gains an explicit discontinuity
operation. Never bridge a timestamp gap by inventing samples in Python.

### Controller/router

Replace process-based local loopbacks with engine route commands when enabled.
The state machine and priority remain unchanged. A route transition is complete
only after the engine acknowledges it. If the engine fails:

- fail silent first;
- restart it with bounded backoff;
- expose a failed component and discontinuity counters;
- permit the legacy path only when explicitly selected by configuration, not
  through an automatic mid-session fallback that could contend for devices.

### Configuration

Add:

```toml
[audio_engine]
backend = "legacy"              # migration default; "timestamped" to enable
socket_path = "/run/phono-console/audio-engine.sock"
frame_ms = 20
output_prebuffer_ms = 80
route_fade_ms = 8
max_soft_correction_ppm = 250
queue_frames = 50
```

Keep existing ALSA device names. Add explicit raw device fields during
migration so the engine never opens a `dsnoop` alias accidentally. Validate
that `backend = "timestamped"` cannot start with the legacy Bluetooth ingest
service enabled.

### Dashboard and API

Expose:

- engine connection/version and active route;
- negotiated format for each source;
- timestamp age and epoch;
- current/max clock error and correction ppm;
- capture/output xruns, discontinuities, and dropped frames;
- per-source queue depth and transport delay; and
- backend (`legacy` or `timestamped`).

Local-only remains a policy setting and must use the same engine; it is not a
switch back to the legacy path.

## Service and installer changes

Add `phono-console-audio-engine.service` with realtime-safe but minimal systemd
permissions, membership in the `audio` and `bluetooth` groups, runtime socket
directory, restart backoff, and journal capture. The Python daemon starts after
the engine socket is available. The standalone Sendspin player remains a
separate service but writes only to the MA return loopback consumed by the
engine.

Installer work:

- install `build-essential`, `libasound2-dev`, and `libsamplerate0-dev`;
- compile with warnings-as-errors and hardening flags;
- run native unit tests before activating a release;
- install the engine into the versioned release directory;
- install/enable its systemd unit;
- stop and disable `phono-console-bluetooth.service` only when the timestamped
  backend is selected;
- omit Bluetooth loopback/dsnoop aliases for a fresh timestamped install;
- preserve legacy ALSA definitions for rollback on upgraded devices;
- extend `check-install.sh` with engine socket, negotiated PCM, timestamp, and
  xrun checks; and
- roll back all services and the `current` symlink atomically if readiness
  fails.

## Staged implementation

### Phase 0 — Freeze and baseline

- Tag the current legacy implementation and record the rollback commit.
- Capture 10-minute local and distributed recordings plus current service/API
  diagnostics.
- Record native Bluetooth rate/codec, UFO202 parameters, drop cadence, clock
  drift, and CPU load.

Exit: reproducible baseline and one-command rollback documented.

### Phase 1 — Timestamp probe

- Implement a read-only native `audio-engine probe DEVICE` command.
- Log timestamp/frame deltas and discontinuities without playing or streaming.
- Test phono, Bluetooth play/pause/reconnect, and BlueALSA restart on the Pi.
- Decide ALSA PCM versus BlueALSA D-Bus capture using the validation gate above.

Exit: measured evidence that the chosen capture boundary supplies usable
timing. Do not implement routing until this passes.

### Phase 2 — Protocol and simulated engine

- Implement/version the packet protocol and golden binary fixtures in C and
  Python.
- Build a synthetic capture clock with controlled drift, burst delivery,
  xruns, gaps, and restarts.
- Implement Python queues, telemetry, and timestamped Sendspin adapter against
  the simulator.

Exit: deterministic cross-language tests and bounded backpressure behavior.

### Phase 3 — Phono vertical slice

- Capture UFO202 once, provide meters/activity, local playback, and Sendspin
  frames from the engine.
- Keep Bluetooth on the legacy backend temporarily, but never allow both
  backends to own the same PCM.
- Compare local latency and whole-house synchronization with the baseline.

Exit: phono priority, local fallback, MA return, and device-recovery tests pass.

### Phase 4 — Bluetooth vertical slice

- Add BlueALSA capture, transport lifecycle, nominal resampling, timestamped
  Sendspin frames, and local adaptive playback.
- Remove FFmpeg and the Bluetooth ALSA loopback from the enabled graph.
- Test SBC at 44.1 and 48 kHz plus any negotiated AAC codec supported by the
  installed BlueALSA build.

Exit: no audible varispeed, no periodic gaps, and bounded correction under the
device test matrix.

### Phase 5 — Unified output and cleanup

- Move MA-return rendering into the engine and remove runtime-managed
  `alsaloop` processes.
- Enforce single physical-output ownership structurally.
- Remove unused legacy configuration only after rollback has been exercised.

Exit: no `EBUSY` transitions and pop-free source preemption/resume.

### Phase 6 — Default-on migration

- Ship timestamped backend opt-in for one full device soak.
- Make it the installer default only after all acceptance tests pass.
- Retain `backend = "legacy"` for one release cycle, then remove it in a
  separately reversible change.

## Test plan

### Native unit tests

- timestamp calculation around ring-buffer wrap;
- proof that injected processing and send delays do not change sample
  timestamps;
- partial reads and variable ALSA periods;
- rational 44.1 -> 48 kHz frame accounting over one hour;
- resampler latency/phase continuity;
- bounded adaptive-ratio controller;
- xrun/suspend/remove recovery and epoch changes;
- queue overflow and discontinuity propagation;
- protocol encode/decode and malformed command rejection; and
- route fades and exclusive sink ownership.

### Python tests

- protocol compatibility and version mismatch;
- source priority/preemption/resume using engine telemetry;
- timestamp forwarding into Sendspin;
- source restart on discontinuity;
- local-only behavior;
- engine disconnect/reconnect and fail-silent behavior;
- dashboard/API health fields; and
- installer migration and rollback.

### On-device acceptance

Run each case with local-only and `Downstairs` distribution:

- phono start/stop and side change;
- Bluetooth play/pause/seek for SBC 44.1 and 48 kHz;
- 60-minute uninterrupted Bluetooth playback;
- phono preempts Bluetooth and Bluetooth resumes;
- MA playback is preempted and resumes according to policy;
- phone disconnect/reconnect and a second paired phone;
- BlueALSA, MA, network, Python daemon, and engine restarts;
- UFO202 unplug/replug; and
- CPU/memory pressure representative of the Pi.

Acceptance thresholds:

- zero audible clicks, periodic gaps, or varispeed in the one-hour run;
- zero unreported discontinuities or output-owner collisions;
- no sustained correction above 250 ppm;
- no unbounded queue growth;
- local phono latency remains below 50 ms;
- dashboard route/meters match the audible source; and
- all rooms remain synchronized within the practical MA/Sendspin calibration
  tolerance.

Capture WAV output and structured telemetry for every failed run. Listening
alone is not sufficient evidence.

## Rollout and rollback

The timestamped backend is opt-in until the soak passes. Activation sequence:

1. Stop Python router, legacy Bluetooth ingest, and Sendspin player.
2. Start the engine and verify its socket/readiness response.
3. Start the Sendspin player and Python daemon.
4. Confirm all configured PCMs and one output owner.
5. Enable automatic routing.

Rollback reverses that sequence, sets `backend = "legacy"`, restores the prior
versioned release symlink and ALSA definitions, restarts the three legacy
services, and verifies local-only Bluetooth before re-enabling distribution.

No installer failure may leave both engines enabled or the physical output
owned by an untracked process.

## Definition of done

The work is complete only when the timestamped backend is the installed
default, legacy FFmpeg/loopback/`arecord` Bluetooth transport is removed, all
automated and on-device acceptance tests pass, one-hour Bluetooth distribution
has no audible or measured timing defect, and rollback has been executed on the
target Pi—not merely documented.
