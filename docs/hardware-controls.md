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

The first bench stage connects only encoder A, encoder B, switch, and ground;
the LED PCB is not required.

## Encoder wiring

Use BCM numbering (not physical header numbers):

| PEC11H terminal | Raspberry Pi 5 |
| --- | --- |
| A | GPIO17, physical pin 11 |
| B | GPIO27, physical pin 13 |
| C/common | Ground, physical pin 14 |
| S or W | GPIO22, physical pin 15 |
| Remaining switch terminal | Ground, physical pin 14 |

The inputs use the Pi's internal pull-ups. Do not connect any encoder terminal
to 5 V. If clockwise rotation lowers the volume, set `reverse = true` rather
than swapping wiring inside the cabinet.

After installing the current release, enable the module in
`/etc/phono-console/config.toml`:

```toml
[controls]
enabled = true
encoder_a_gpio = 17
encoder_b_gpio = 27
encoder_button_gpio = 22
volume_step = 2
reverse = false
encoder_bounce_ms = 2
button_bounce_ms = 40
```

Then restart `phono-console.service`. The service reports `encoder ready` in
the dashboard health data and emits structured turn, press, volume, and mode
events to its journal. GPIO setup failure degrades only the hardware-control
component; audio routing continues to run.

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

The GPIO worker is disabled by default, uses the Pi GPIO character device, and
coalesces rapid detents before making Music Assistant network calls. Rotation
changes local Yamaha volume during local playback and the Downstairs group
volume during synchronized playback. The push switch toggles phono between
Console and Downstairs, pauses Bluetooth, stops ordinary MA playback, and does
nothing while idle.
