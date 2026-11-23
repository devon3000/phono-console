# Yamaha volume calibration

Measured SR-300 front-panel and CEC volumes agree: quiet 12 (`0c`), normal
20 (`14`), loud 40 (`28`). The calibrated curve maps MA 1 to 12, MA 60 to 20,
and MA 100 to 40, with linear interpolation on either side. Zero is mute.
Integer CEC steps remain: these are 29 hardware levels, not 100 independent
gain settings. Remote changes outside this range remain visible as raw
`cec_volume`; the logical slider saturates at its calibrated endpoint. Reading
the receiver never moves it back inside the range.

```toml
[amplifier]
volume_min = 12
volume_mid = 20
volume_mid_position = 60
volume_max = 40
```

These are now the defaults when no amplifier endpoints are configured, as on
this installation. Explicit existing min/max pairs without a midpoint retain
their previous linear mapping. Fresh installer configurations include all four
calibration fields. Existing configuration and tokens are not overwritten.
Bluetooth's separate 20–80 phone-to-output mapping is unchanged.

Keep the Phono Console player limits in MA at 0–100: do not apply the previously
suggested maximum of 23 on top of this curve. Normal playback is now around MA
60 rather than 14; initially lower the volume and raise it by ear. Existing MA
and local-phono saved numbers are not rewritten. The local-phono profile and
MA playback still own their respective volume restoration at route boundaries.

One persistent CEC client now has a continuous stdout reader. Audio status
reports refresh the dashboard, including unsolicited reports. A read-only
background task queries power and (only when powered on) volume, then waits
three seconds. It shares the command lock, skips busy command sessions, and
never sends wake, volume, mute, or input commands. Failed refreshes mark status
degraded rather than silently retaining an apparently healthy cached value.
The reporting path does not send remote changes back to MA or persist them as
a new local-phono startup profile.

The dashboard's `volume` is the calibrated logical value; `cec_volume` is the
actual receiver value. For example a front-panel setting of 20 reports MA 60.
Ordinary refreshes must not change the front-panel setting. No timing/audio
routing or group-membership settings are changed.
