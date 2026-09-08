from __future__ import annotations

from .policy import Route
from .processes import ManagedProcess


class ProcessAudioRouter:
    """Own the local-loopback lifecycle for the console output.

    Music Assistant playback (including the returned whole-house stream) is
    rendered by the separate Sendspin player service, and whole-house capture
    publication is command-driven inside the Sendspin source publisher, so the
    loopback is the only process the routing decision has to manage.
    """

    def __init__(self, local_loopback: ManagedProcess) -> None:
        self.local_loopback = local_loopback

    async def apply(self, route: Route) -> None:
        if route is Route.LOCAL_PHONO:
            await self.local_loopback.start()
        else:
            await self.local_loopback.stop()

    async def reconcile(self, route: Route) -> None:
        """Repair drift without treating an unchanged route as a transition."""
        await self.apply(route)

    async def close(self) -> None:
        await self.local_loopback.stop()
