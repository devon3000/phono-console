# PCM-aware Sendspin player recovery

## Captured incident

On November 22 the cabinet player (Sendspin 7.5.0) reported sync error growing
from 242 ms to 503 ms, then scheduled a reanchor at 18:37:16 Pacific. Its error
returned near zero while the rooms remained audibly offset. The Pi route stayed
`distributed_bluetooth`; the MA-return ALSA process, owners, and trigger times
remained unchanged. No source restart or ALSA xrun was logged in that interval.
MA also logged Living Room control-request failures afterward; those are not
proven to be the cause of the cabinet's retained offset.

The installed callback was verified directly: its reanchor assigned the
DAC-derived server time to the read cursor and reset the filter without moving
the underlying PCM. That can erase the reported error while leaving old audio
at the read head. This fix addresses that recovery defect, not every possible
network interruption or receiver failure.

## Change and boundaries

The separate player wrapper applies a narrowly checked in-memory correction to
Sendspin 7.5.0 before starting the CLI. No installed package file is rewritten.
The installer pins this version and validates the patch before activating a new
release; mismatched version/source fails explicitly, rather than silently
patching an unknown implementation. Repeated application is a no-op.

- Large-error correction explicitly marks a recovery, distinct from startup.
- Behind: use the existing frame-skip routine to discard expired PCM. Chunk
  offsets, fractional cursor, and queue-duration bookkeeping move together.
- Ahead: emit silence until the queued PCM is due, including partial callbacks.
  Do not consume future audio or relabel its cursor.
- Insufficient input: retain pending recovery and emit silence; do not claim
  success by assigning the desired timestamp to audio not yet consumed.
- Clear and format changes cancel the recovery marker.
- Normal startup DAC anchoring, static delay (180 ms on this cabinet), ordinary
  speed correction, source publication and physical output routing are unchanged.

Recovery can briefly skip content or wait silently. This is deliberate: a large
timing error should produce a bounded audible interruption, not persistent
misalignment hidden by a zeroed error counter. Silence remains possible while
input is unavailable; this is not a guarantee against all dropouts.

## Verification

Regression tests reproduce the old false-success result using numbered PCM
samples, then verify actual sample selection at 44.1 and 48 kHz, partial chunks,
ahead/behind recovery, empty-queue refill, startup, explicit clear, and patch
compatibility/idempotence. The patch was also exercised against the exact
upstream 7.5.0 callback and queue routines (without a physical audio device).

After installation, the player journal must contain:
`PCM-aware reanchor recovery enabled for Sendspin 7.5.0`.
Repeat the Bluetooth dropout/recovery test on the Pi; unit tests cannot establish
acoustic alignment with AirPlay speakers. Do not compensate this failure by
changing the static delay. The paused Yamaha volume calibration is not included.
