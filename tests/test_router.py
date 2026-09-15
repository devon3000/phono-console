import asyncio

from phono_console.policy import Route
from phono_console.processes import ManagedProcess, ProcessSpec
from phono_console.router import ProcessAudioRouter
from phono_console.simulation import SimulatedEventSink
from test_processes import FakeLauncher


def test_router_runs_loopback_only_for_local_phono() -> None:
    async def scenario() -> None:
        events = SimulatedEventSink()
        loop = ManagedProcess(ProcessSpec("loopback", ("loop",)), FakeLauncher(), events)
        router = ProcessAudioRouter(loop)

        await router.apply(Route.LOCAL_PHONO)
        assert loop.running

        await router.apply(Route.WHOLE_HOUSE_PHONO)
        assert not loop.running

        await router.apply(Route.LOCAL_PHONO)
        await router.apply(Route.MA_PLAYBACK)
        assert not loop.running

        await router.apply(Route.LOCAL_PHONO)
        await router.close()
        assert not loop.running

    asyncio.run(scenario())


def test_router_exclusively_runs_the_selected_local_fallback() -> None:
    async def scenario() -> None:
        events = SimulatedEventSink()
        phono = ManagedProcess(ProcessSpec("phono", ("phono",)), FakeLauncher(), events)
        bluetooth = ManagedProcess(ProcessSpec("bluetooth", ("bluetooth",)), FakeLauncher(), events)
        router = ProcessAudioRouter(phono, bluetooth)

        await router.apply(Route.LOCAL_BLUETOOTH)
        assert bluetooth.running and not phono.running
        await router.apply(Route.LOCAL_PHONO)
        assert phono.running and not bluetooth.running
        await router.apply(Route.DISTRIBUTED_PHONO)
        assert not phono.running and not bluetooth.running

    asyncio.run(scenario())
