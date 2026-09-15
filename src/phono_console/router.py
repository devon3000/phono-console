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

    def __init__(
        self,
        local_loopback: ManagedProcess,
        bluetooth_loopback: ManagedProcess | None = None,
        ma_loopback: ManagedProcess | None = None,
    ) -> None:
        self.local_loopback = local_loopback
        self.bluetooth_loopback = bluetooth_loopback
        self.ma_loopback = ma_loopback

    async def apply(self, route: Route) -> None:
        phono_needed = route is Route.LOCAL_PHONO
        bluetooth_needed = route is Route.LOCAL_BLUETOOTH
        ma_needed = route in {
            Route.MA_PLAYBACK,
            Route.DISTRIBUTED_PHONO,
            Route.DISTRIBUTED_BLUETOOTH,
        }

        # Every loopback targets the same exclusive hardware PCM. Stop the
        # old owner before starting the new one; starting in attribute order
        # made MA -> Bluetooth briefly overlap and fail with EBUSY.
        if not phono_needed:
            await self.local_loopback.stop()
        if self.bluetooth_loopback is not None and not bluetooth_needed:
            await self.bluetooth_loopback.stop()
        if self.ma_loopback is not None and not ma_needed:
            await self.ma_loopback.stop()

        if phono_needed:
            await self.local_loopback.start()
        elif bluetooth_needed and self.bluetooth_loopback is not None:
            await self.bluetooth_loopback.start()
        elif ma_needed and self.ma_loopback is not None:
            await self.ma_loopback.start()

    async def reconcile(self, route: Route) -> None:
        """Repair drift without treating an unchanged route as a transition."""
        await self.apply(route)

    async def close(self) -> None:
        await self.local_loopback.stop()
        if self.bluetooth_loopback is not None:
            await self.bluetooth_loopback.stop()
        if self.ma_loopback is not None:
            await self.ma_loopback.stop()
