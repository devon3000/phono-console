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
