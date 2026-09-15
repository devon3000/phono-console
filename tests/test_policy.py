from phono_console.policy import Inputs, Route, choose_route


def test_idle_when_no_source_is_active() -> None:
    assert choose_route(Inputs()) is Route.IDLE


def test_phono_activity_selects_local_loopback() -> None:
    assert choose_route(Inputs(phono_active=True)) is Route.LOCAL_PHONO


def test_phono_takes_priority_over_ma_playback() -> None:
    inputs = Inputs(phono_active=True, ma_playing=True)
    assert choose_route(inputs) is Route.LOCAL_PHONO


def test_bluetooth_takes_priority_over_ma_but_not_phono() -> None:
    assert choose_route(Inputs(bluetooth_active=True, ma_playing=True)) is Route.LOCAL_BLUETOOTH
    assert choose_route(Inputs(phono_active=True, bluetooth_active=True)) is Route.LOCAL_PHONO


def test_available_distribution_routes_local_sources_through_ma() -> None:
    assert choose_route(
        Inputs(phono_active=True, distribution_available=True)
    ) is Route.DISTRIBUTED_PHONO
    assert choose_route(
        Inputs(bluetooth_active=True, distribution_available=True)
    ) is Route.DISTRIBUTED_BLUETOOTH
