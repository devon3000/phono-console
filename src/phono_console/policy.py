from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Route(StrEnum):
    IDLE = "idle"
    LOCAL_PHONO = "local_phono"
    MA_PLAYBACK = "ma_playback"
    WHOLE_HOUSE_PHONO = "whole_house_phono"


@dataclass(frozen=True)
class Inputs:
    phono_active: bool = False
    ma_playing: bool = False
    whole_house_requested: bool = False


def choose_route(inputs: Inputs) -> Route:
    """Choose a route using the project's source-priority contract."""
    if inputs.whole_house_requested:
        return Route.WHOLE_HOUSE_PHONO
    if inputs.ma_playing:
        return Route.MA_PLAYBACK
    if inputs.phono_active:
        return Route.LOCAL_PHONO
    return Route.IDLE

