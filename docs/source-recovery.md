# Source discontinuity recovery

## Observed failure

On 2026-11-16 at 18:45:38 Pacific, the Bluetooth Sendspin source aborted on a
timestamped PCM discontinuity. MA stopped the group about one second later and
the cabinet fell back to local Bluetooth. Both control connections remained up.

The discontinuity itself can originate upstream (capture recovery or a native
transport gap), or from a full subscriber queue. The existing log did not retain
enough provenance to establish which happened in this incident.

## Recovery defect and fix

The failed pump publishes `streaming=false, stream_requested=true` and schedules
a delayed retry. When MA then sends `source.stop`, `_stop_streaming` cancels the
retry and clears the internal request. Previously it only published the new state
if it had stopped an actively streaming pump. Since this pump had already failed,
the controller retained the stale request and error. Runtime's capability gate
then refused a fresh distribution session: a stream appeared requested but was
unhealthy. This could leave Downstairs requested indefinitely with local output.

Always publish state after stop cleanup, even when the pump has already stopped.
The controller can then request a fresh MA group session using the existing
output intent, source activity checks, and preparation timeout. A server stop is
still honored and does not itself reopen capture. Explicit Console selection
still disables distribution and cancels retries.

Do not resume an old capture across a timing break or independently restart the
cabinet's MA-return timeline. Existing discontinuity abort behavior is retained;
the normal MA session-start path owns synchronized distribution recovery.

## Diagnostics

### Phono-to-Bluetooth handoff

The November 17 capture showed `local_bluetooth` / `starting_distribution`
despite MA playing, a healthy Bluetooth source session, and Downstairs requested.
The cabinet's MA-return loopback was absent: the audible offset in this capture
was direct local playback alongside MA's delayed distribution, not player clock
drift. Phono was quiet at about -69 dBFS but its long release latch remained true.

Runtime readiness had independently chosen phono from that release latch while
the controller correctly selected Bluetooth from current activity. Readiness now
receives the controller's current-tick source explicitly. Source identity,
session generation, stream health, and both MA playback confirmations remain
required. The existing short Bluetooth telemetry-gap protection also uses this
selection, so a quiet phono release latch cannot disable it and reopen the return
loopback. Actual renewed phono signal retains priority.

This fixes the reproduced handoff/fallback mismatch. The separate earlier MA
"no audio for 60s" event is not explained by this readiness defect; no capture
or Sendspin timing parameters are changed by this fix.

### PCM discontinuities

- `sendspin_source_discontinuity` records source, sequence, epoch, timestamp,
  flags, already-published sample count, and subscriber-overrun provenance.
- `active_subscriber_drops` counts queue evictions affecting subscribed consumers.
- `idle_queue_drops` counts evictions from the unused primary backlog.
- `dropped_frames` remains the legacy aggregate queue-eviction count, not a
  Bluetooth radio packet-loss measurement. Counts refer to PCM frame messages,
  not individual audio samples. A message may be evicted by multiple subscribers.
- Subscriber-overrun provenance stays in the Python frame object; the native
  wire format and protocol version are unchanged. An overrun does not exclude
  an upstream timing break occurring on the same frame.

## Verification and limitations

Regression tests cover real timestamped discontinuities (with and without queue
overrun), the following MA stop canceling retry, fresh state publication, and
controller recovery from distributed Bluetooth through local fallback to a new
distributed session. Fanout tests verify a slow subscriber does not contaminate
another subscriber's frames and distinguish active from idle evictions.

This fixes a reproduced permanent-fallback defect. It does not establish or fix
the origin of the intermittent discontinuity, nor prove the separate abrupt
multi-second desynchronization is resolved. Receiver/network recovery and timing
still require testing on the Pi and MA installation.
