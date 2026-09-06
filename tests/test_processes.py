import asyncio
from dataclasses import dataclass

from phono_console.processes import ManagedProcess, ProcessSpec
from phono_console.simulation import SimulatedEventSink


@dataclass
class FakeProcess:
    returncode: int | None = None
    terminated: bool = False
    killed: bool = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        assert self.returncode is not None
        return self.returncode


class FakeLauncher:
    def __init__(self) -> None:
        self.processes: list[FakeProcess] = []

    async def start(self, argv: tuple[str, ...]) -> FakeProcess:
        process = FakeProcess()
        self.processes.append(process)
        return process


def test_managed_process_is_idempotent_and_reports_lifecycle() -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        events = SimulatedEventSink()
        process = ManagedProcess(ProcessSpec("loopback", ("fake",)), launcher, events)
        await process.start()
        await process.start()
        assert len(launcher.processes) == 1
        await process.stop()
        assert launcher.processes[0].terminated
        assert [name for name, _ in events.events] == [
            "audio_process_started",
            "audio_process_stopped",
        ]

    asyncio.run(scenario())

