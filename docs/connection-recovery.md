# Connection-loss recovery

## November 23 evidence

The source logged a three-second audio-write stall at 19:10:41 Pacific. Both
source and player then lost WebSocket heartbeats, on both ends of the link.
They reconnected at 19:11:22–26. The controller subsequently received a cancelled
MA stop-response Future; cleanup re-raised an already failed listener exception,
and systemd restarted the router. MA rejected source audio after restart because
no input stream was open, then timed it out after 60 seconds.

This establishes recovery failures, not the cause of the original connection
stall. These changes do not claim to fix Wi-Fi, MA server stalls, or every
AirPlay receiver error.

## Changes

- MA stop has a bounded deadline (the configured distribution-start timeout,
  five seconds by default). A cancelled remote response is a connection error;
  an actually cancelled caller Task still propagates cancellation.
- The controller also distinguishes remote-Future cancellation from its own
  shutdown. Failed MA listener/disconnect cleanup no longer masks the failure
  with another exception.
- Failed distribution is released BEFORE replacement preparation. The old
  ordering could start a new source and then stop it during old-session cleanup.
- Ending or failing a source capture revokes its local requested state. A local
  timer cannot create another capture: the controller requests a new MA session,
  and only a fresh server `source.start` opens capture. Source changes follow
  the same rule; selected output intent still belongs to the routing state.
- Command callbacks carry their connection generation. Queued start/stop from
  an old connection cannot open or close capture on its replacement.
- Capture shutdown is bounded so a failed write cannot indefinitely prevent
  publication of the restartable source state.

Existing Bluetooth short-telemetry-gap preservation remains: a healthy source
does not lose its cabinet return merely because MA telemetry briefly lags.
Explicit Console selection and genuine MA Stop remain authoritative. Static
delay, audio buffers, volume calibration and PCM-aware player recovery are
unchanged.

Regression coverage includes cancelled response Futures, real Task shutdown,
bounded stop, failed cleanup, capture EOF/discontinuity, fresh-start-only source
handoff, stale connection commands, and controller recovery with a surviving
replacement stream. Test on the Pi after installation; no MA restart is needed.

This supersedes the local delayed-capture-retry behavior described historically
in `source-recovery.md`: ending a stream consumes its server admission.
