from __future__ import annotations

from .policy import Route
from .processes import ManagedProcess


class ProcessAudioRouter:
    """Own the local-loopback and whole-house-source process lifecycles."""

    def __init__(
        self,
        local_loopback: ManagedProcess,
        whole_house_source: ManagedProcess,
    ) -> None:
        self.local_loopback = local_loopback
        self.whole_house_source = whole_house_source

    async def apply(self, route: Route) -> None:
        # Stop conflicting capture consumers before starting the selected one.
        if route is not Route.LOCAL_PHONO:
            await self.local_loopback.stop()
        if route is not Route.WHOLE_HOUSE_PHONO:
            await self.whole_house_source.stop()

        if route is Route.LOCAL_PHONO:
            await self.whole_house_source.stop()
            await self.local_loopback.start()
        elif route is Route.WHOLE_HOUSE_PHONO:
            await self.local_loopback.stop()
            await self.whole_house_source.start()

    async def close(self) -> None:
        await self.local_loopback.stop()
        await self.whole_house_source.stop()

