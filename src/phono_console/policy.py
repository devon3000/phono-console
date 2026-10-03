from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Route(StrEnum):
    IDLE = "idle"
    LOCAL_PHONO = "local_phono"
    LOCAL_BLUETOOTH = "local_bluetooth"
    MA_PLAYBACK = "ma_playback"
    DISTRIBUTED_PHONO = "distributed_phono"
    DISTRIBUTED_BLUETOOTH = "distributed_bluetooth"
    # Compatibility for persisted/UI values from the vinyl-only design.
    WHOLE_HOUSE_PHONO = "distributed_phono"


class Source(StrEnum):
    NONE = "none"
    PHONO = "phono"
    BLUETOOTH = "bluetooth"
    MUSIC_ASSISTANT = "music_assistant"


class DistributionPath(StrEnum):
    NONE = "none"
    MA_DOWNSTAIRS = "ma_downstairs"
    LOCAL_FALLBACK = "local_fallback"


class PhonoOutputMode(StrEnum):
    LOCAL = "local"
    DOWNSTAIRS = "downstairs"


class OutputTarget(StrEnum):
    """The single user-controlled output destination."""

    CONSOLE = "console"
    DOWNSTAIRS = "downstairs"


class RoutingPhase(StrEnum):
    STABLE = "stable"
    STARTING_DISTRIBUTION = "starting_distribution"
    STOPPING_DISTRIBUTION = "stopping_distribution"
    DEGRADED = "degraded"


class DistributionCommand(StrEnum):
    NONE = "none"
    START = "start"
    STOP = "stop"


@dataclass(frozen=True)
class RoutingObservation:
    active_source: Source = Source.NONE
    ma_console_playing: bool = False
    ma_target_playing: bool = False
    ma_connected: bool = False
    sendspin_connected: bool = False
    sendspin_stream_requested: bool = False
    sendspin_streaming: bool = False
    sendspin_stream_healthy: bool = False
    distribution_capable: bool = False
    selected_source: Source = Source.NONE
    session_generation: int | None = None


@dataclass(frozen=True)
class RoutingDecision:
    route: Route
    phase: RoutingPhase
    command: DistributionCommand = DistributionCommand.NONE
    confirmed: bool = False
    error: str | None = None


def distribution_confirmed(
    observation: RoutingObservation, *, generation: int
) -> bool:
    """Return true only when every control-plane distribution signal agrees."""
    return bool(
        observation.active_source in {Source.PHONO, Source.BLUETOOTH}
        and observation.selected_source is observation.active_source
        and observation.sendspin_connected
        and observation.sendspin_stream_requested
        and observation.sendspin_streaming
        and observation.sendspin_stream_healthy
        and observation.ma_connected
        and observation.ma_console_playing
        and observation.ma_target_playing
        and observation.session_generation == generation
    )


def reduce_routing(
    target: OutputTarget,
    observation: RoutingObservation,
    *,
    generation: int,
    distribution_session_active: bool = False,
) -> RoutingDecision:
    """Pure routing reducer; side effects belong to the transition executor."""
    source = observation.active_source
    if source is Source.NONE:
        return RoutingDecision(
            Route.MA_PLAYBACK if observation.ma_console_playing else Route.IDLE,
            RoutingPhase.STABLE,
            (
                DistributionCommand.STOP
                if distribution_session_active
                else DistributionCommand.NONE
            ),
        )

    local_route = (
        Route.LOCAL_PHONO
        if source is Source.PHONO
        else Route.LOCAL_BLUETOOTH
    )
    if target is OutputTarget.CONSOLE:
        return RoutingDecision(
            local_route,
            RoutingPhase.STOPPING_DISTRIBUTION
            if distribution_session_active
            else RoutingPhase.STABLE,
            DistributionCommand.STOP
            if distribution_session_active
            else DistributionCommand.NONE,
        )

    if distribution_confirmed(observation, generation=generation):
        return RoutingDecision(
            Route.DISTRIBUTED_PHONO
            if source is Source.PHONO
            else Route.DISTRIBUTED_BLUETOOTH,
            RoutingPhase.STABLE,
            confirmed=True,
        )
    if observation.distribution_capable:
        return RoutingDecision(
            local_route,
            RoutingPhase.STARTING_DISTRIBUTION,
            DistributionCommand.START,
        )
    return RoutingDecision(
        local_route,
        RoutingPhase.DEGRADED,
        error="Downstairs distribution is unavailable; playing locally",
    )


@dataclass(frozen=True)
class Inputs:
    phono_active: bool = False
    bluetooth_active: bool = False
    ma_playing: bool = False
    distribution_available: bool = False
    phono_output_mode: PhonoOutputMode = PhonoOutputMode.LOCAL


def choose_route(inputs: Inputs) -> Route:
    """Choose a route using the project's source-priority contract."""
    if inputs.phono_active:
        return (
            Route.DISTRIBUTED_PHONO
            if inputs.distribution_available
            and inputs.phono_output_mode is PhonoOutputMode.DOWNSTAIRS
            else Route.LOCAL_PHONO
        )
    if inputs.bluetooth_active:
        return (
            Route.DISTRIBUTED_BLUETOOTH
            if inputs.distribution_available
            else Route.LOCAL_BLUETOOTH
        )
    if inputs.ma_playing:
        return Route.MA_PLAYBACK
    return Route.IDLE


def route_source(route: Route) -> Source:
    if route in {Route.LOCAL_PHONO, Route.DISTRIBUTED_PHONO}:
        return Source.PHONO
    if route in {Route.LOCAL_BLUETOOTH, Route.DISTRIBUTED_BLUETOOTH}:
        return Source.BLUETOOTH
    if route is Route.MA_PLAYBACK:
        return Source.MUSIC_ASSISTANT
    return Source.NONE


def route_distribution(route: Route) -> DistributionPath:
    if route in {Route.DISTRIBUTED_PHONO, Route.DISTRIBUTED_BLUETOOTH}:
        return DistributionPath.MA_DOWNSTAIRS
    if route in {Route.LOCAL_PHONO, Route.LOCAL_BLUETOOTH}:
        return DistributionPath.LOCAL_FALLBACK
    return DistributionPath.NONE
