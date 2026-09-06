from phono_console.policy import Inputs, Route, choose_route


def test_idle_when_no_source_is_active() -> None:
    assert choose_route(Inputs()) is Route.IDLE


def test_phono_activity_selects_local_loopback() -> None:
    assert choose_route(Inputs(phono_active=True)) is Route.LOCAL_PHONO


def test_ma_playback_takes_priority_over_local_phono() -> None:
    inputs = Inputs(phono_active=True, ma_playing=True)
    assert choose_route(inputs) is Route.MA_PLAYBACK


def test_whole_house_request_has_highest_priority() -> None:
    inputs = Inputs(phono_active=True, ma_playing=True, whole_house_requested=True)
    assert choose_route(inputs) is Route.WHOLE_HOUSE_PHONO

