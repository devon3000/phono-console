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
