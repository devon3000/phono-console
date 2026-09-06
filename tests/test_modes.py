from phono_console.modes import Mode, desired_state


def test_local_vinyl_uses_direct_phono_input() -> None:
    state = desired_state(Mode.LOCAL_VINYL)
    assert state.amplifier_input == "phono"
    assert not state.capture_enabled


def test_broadcast_vinyl_routes_console_through_stream() -> None:
    state = desired_state(Mode.BROADCAST_VINYL)
    assert state.amplifier_input == "pi"
    assert state.capture_enabled
    assert state.console_stream_enabled
    assert state.house_stream_enabled


def test_music_assistant_does_not_capture_phono() -> None:
    state = desired_state(Mode.MUSIC_ASSISTANT)
    assert state.amplifier_input == "pi"
    assert not state.capture_enabled

