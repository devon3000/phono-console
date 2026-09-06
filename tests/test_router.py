import asyncio

from phono_console.policy import Route
from phono_console.processes import ManagedProcess, ProcessSpec
from phono_console.router import ProcessAudioRouter
from phono_console.simulation import SimulatedEventSink
from test_processes import FakeLauncher


def test_router_exclusively_switches_capture_consumers() -> None:
    async def scenario() -> None:
        events = SimulatedEventSink()
        loop = ManagedProcess(ProcessSpec("loopback", ("loop",)), FakeLauncher(), events)
        source = ManagedProcess(ProcessSpec("source", ("source",)), FakeLauncher(), events)
        router = ProcessAudioRouter(loop, source)

        await router.apply(Route.LOCAL_PHONO)
        assert loop.running and not source.running

        await router.apply(Route.WHOLE_HOUSE_PHONO)
        assert source.running and not loop.running

        await router.apply(Route.MA_PLAYBACK)
        assert not source.running and not loop.running

    asyncio.run(scenario())
