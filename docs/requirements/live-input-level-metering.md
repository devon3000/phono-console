# Feature: Live input level metering and calibration

Add a diagnostic/calibration mode for the UFO202 capture input so the user can
verify phono input levels and detect clipping.

## Requirements

- Read the same stereo PCM capture source used for turntable input; require no
  additional hardware or second physical capture.
- Calculate independent left/right peak levels in dBFS.
- Display live stereo terminal bars and numerical dBFS values.
- Track maximum left/right peaks from start or reset.
- Treat full-scale samples as clipping and latch independent L/R clip indicators
  until reset.
- Provide `phono-console levels --config PATH`.
- Print maximum levels and clipping history when the command exits.
- Display short-window RMS while peak/max peak remains authoritative.
- Never normalize, resample, or modify PCM while metering.
- Permit the production daemon to expose meter readings from its shared capture
  source so calibration does not require a second ALSA owner.

## Calibration objective

Play representative loud records and evaluate ADC output headroom. The UFO202
phono gain is fixed; consistently low or clipping peaks must be addressed in the
analog front end.

## Acceptance test

Feed a stereo signal, run the meter for several minutes, verify independent L/R
response, then approach full scale and confirm maximum peaks and persistent clip
indicators.

