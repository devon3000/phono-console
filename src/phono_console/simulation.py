from __future__ import annotations

from dataclasses import dataclass, field

from .policy import Route


@dataclass
class SimulatedLevelMonitor:
    level: float = -120.0

    async def level_dbfs(self) -> float:
        return self.level


@dataclass
class SimulatedMusicAssistant:
    playing: bool = False
    whole_house: bool = False

    async def console_is_playing(self) -> bool:
        return self.playing

    async def whole_house_is_requested(self) -> bool:
        return self.whole_house


@dataclass
class SimulatedAudioRouter:
    routes: list[Route] = field(default_factory=list)
    closed: bool = False

    async def apply(self, route: Route) -> None:
        self.routes.append(route)

    async def close(self) -> None:
        self.closed = True

