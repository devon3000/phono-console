from __future__ import annotations

from typing import Protocol

from .policy import Route


class LevelMonitor(Protocol):
    async def level_dbfs(self) -> float: ...


class MusicAssistant(Protocol):
    async def console_is_playing(self) -> bool: ...

    async def whole_house_is_requested(self) -> bool: ...


class AudioRouter(Protocol):
    async def apply(self, route: Route) -> None: ...

    async def close(self) -> None: ...

