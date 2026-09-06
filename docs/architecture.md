# Architecture

## Core rule

The local console may use the direct analog path only when vinyl is local-only.
When vinyl is sent throughout the house, the console must join the same Music
Assistant stream so that all rooms share the same buffering and timing.

## Signal paths

### Local vinyl

```text
Turntable → phono preamp → input switch → amplifier → console speakers
```

The UFO202 and Music Assistant do not participate.

### Broadcast vinyl

```text
Turntable → phono preamp → UFO202 → Raspberry Pi → Music Assistant
                                                    ├→ other rooms
                                                    └→ Pi output → amplifier → console speakers
```

### Music Assistant playback

```text
Music Assistant → Pi output → amplifier → console speakers
```

## Pi responsibilities

The Pi is the traffic cop, not the amplifier. It will eventually:

1. Maintain one of the three named modes.
2. Select direct-phono or Pi audio at the console amplifier.
3. Start and stop the UFO202 capture pipeline.
4. Ask Music Assistant to start or stop the vinyl stream/player group.
5. Expose mode selection and status to Home Assistant.
6. Restore a safe, predictable mode after restart.

## Open integration decisions

- Music Assistant input mechanism and API calls
- Pi audio-output device
- Physical input-switch/relay hardware
- Whether amplifier power remains on or is separately controlled
- Final Raspberry Pi model
- Acceptable capture and distribution latency after bench measurement

