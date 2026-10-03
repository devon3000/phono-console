import pytest

from phono_console.policy import (
    DistributionCommand,
    Inputs,
    OutputTarget,
    PhonoOutputMode,
    Route,
    RoutingObservation,
    RoutingPhase,
    Source,
    choose_route,
    reduce_routing,
)


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
        Inputs(
            phono_active=True,
            distribution_available=True,
            phono_output_mode=PhonoOutputMode.DOWNSTAIRS,
        )
    ) is Route.DISTRIBUTED_PHONO
    assert choose_route(
        Inputs(bluetooth_active=True, distribution_available=True)
    ) is Route.DISTRIBUTED_BLUETOOTH


def test_phono_defaults_local_even_when_distribution_is_available() -> None:
    assert choose_route(
        Inputs(phono_active=True, distribution_available=True)
    ) is Route.LOCAL_PHONO


@pytest.mark.parametrize(
    ("source", "target", "capable", "confirmed", "route", "phase", "command"),
    [
        (Source.NONE, OutputTarget.CONSOLE, False, False, Route.IDLE, RoutingPhase.STABLE, DistributionCommand.NONE),
        (Source.PHONO, OutputTarget.CONSOLE, True, False, Route.LOCAL_PHONO, RoutingPhase.STABLE, DistributionCommand.NONE),
        (Source.BLUETOOTH, OutputTarget.CONSOLE, True, False, Route.LOCAL_BLUETOOTH, RoutingPhase.STABLE, DistributionCommand.NONE),
        (Source.PHONO, OutputTarget.DOWNSTAIRS, True, False, Route.LOCAL_PHONO, RoutingPhase.STARTING_DISTRIBUTION, DistributionCommand.START),
        (Source.BLUETOOTH, OutputTarget.DOWNSTAIRS, True, False, Route.LOCAL_BLUETOOTH, RoutingPhase.STARTING_DISTRIBUTION, DistributionCommand.START),
        (Source.PHONO, OutputTarget.DOWNSTAIRS, False, False, Route.LOCAL_PHONO, RoutingPhase.DEGRADED, DistributionCommand.NONE),
        (Source.BLUETOOTH, OutputTarget.DOWNSTAIRS, False, False, Route.LOCAL_BLUETOOTH, RoutingPhase.DEGRADED, DistributionCommand.NONE),
        (Source.PHONO, OutputTarget.DOWNSTAIRS, True, True, Route.DISTRIBUTED_PHONO, RoutingPhase.STABLE, DistributionCommand.NONE),
        (Source.BLUETOOTH, OutputTarget.DOWNSTAIRS, True, True, Route.DISTRIBUTED_BLUETOOTH, RoutingPhase.STABLE, DistributionCommand.NONE),
    ],
)
def test_routing_reducer_decision_table(
    source, target, capable, confirmed, route, phase, command
) -> None:
    observation = RoutingObservation(
        active_source=source,
        distribution_capable=capable,
        selected_source=source,
        session_generation=7 if confirmed else None,
        ma_console_playing=confirmed,
        ma_target_playing=confirmed,
        ma_connected=confirmed,
        sendspin_connected=confirmed,
        sendspin_stream_requested=confirmed,
        sendspin_streaming=confirmed,
        sendspin_stream_healthy=confirmed,
    )
    decision = reduce_routing(target, observation, generation=7)
    assert (decision.route, decision.phase, decision.command) == (
        route,
        phase,
        command,
    )


def test_distribution_confirmation_rejects_stale_generation() -> None:
    observation = RoutingObservation(
        active_source=Source.BLUETOOTH,
        selected_source=Source.BLUETOOTH,
        session_generation=6,
        distribution_capable=True,
        ma_console_playing=True,
        ma_target_playing=True,
        ma_connected=True,
        sendspin_connected=True,
        sendspin_stream_requested=True,
        sendspin_streaming=True,
        sendspin_stream_healthy=True,
    )
    decision = reduce_routing(
        OutputTarget.DOWNSTAIRS, observation, generation=7
    )
    assert decision.route is Route.LOCAL_BLUETOOTH
    assert decision.phase is RoutingPhase.STARTING_DISTRIBUTION
