# Cabinet hardware controls

## Mechanical plan

- Mount the PEC11H rotary encoder to the false-drawer wood through a rear
  counterbore; do not make the LED PCB carry knob loads.
- Reuse the former light-pipe location above the false handle for the encoder.
- Place three 1 mm panel light pipes in a straight horizontal line below the
  handle. A small rear PCB carries only the LEDs, resistors, connector, and any
  required LED driver parts.
- Connect the controls to the Raspberry Pi with one detachable keyed cable.

Final drilling dimensions must be taken from a full-size paper template on the
cabinet before ordering the LED PCB.

## Encoder behavior

The existing `PUT /v1/amplifier/volume` endpoint is the single volume-control
entry point. Encoder turns will call that endpoint so the current route decides
whether volume belongs to the local Yamaha or to the Music Assistant
Downstairs group. The encoder must not manipulate ALSA gain directly.

The push switch toggles output routing rather than source selection:

- phono: Console ↔ Synchronized Downstairs;
- active Downstairs/Bluetooth playback: stop the active distributed playback;
- idle: no action.

Software GPIO activation is intentionally deferred until the actual BCM pin
assignment and connector pinout are selected. This prevents an installer
upgrade from claiming or driving arbitrary Raspberry Pi pins. The first bench
stage can connect only encoder A, encoder B, switch, and ground; the LED PCB is
not required.

## Indicators

The three indicators retain the agreed state language:

| Red | Blue | Yellow | Meaning |
| --- | --- | --- | --- |
| on | off | off | phono playing locally |
| on | off | on | phono playing Downstairs |
| off | on | on | Bluetooth playing Downstairs |
| off | off | on | Music Assistant playing Downstairs |

Transient connection and failure states should blink the affected source LED;
the normal steady combinations remain unambiguous without labels.

## Before enabling GPIO

Record these decisions in configuration and the installation check:

1. BCM pin for encoder A;
2. BCM pin for encoder B;
3. BCM pin for the push switch;
4. connector pin order, including ground;
5. whether the chosen encoder breakout needs external pull-ups; and
6. direction convention when viewed from the cabinet front.

Then add a disabled-by-default GPIO worker, quadrature/debounce tests, service
permissions for the Pi GPIO character device, and an installer wiring check.
