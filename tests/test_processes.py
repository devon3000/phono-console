import asyncio
from dataclasses import dataclass

from phono_console.processes import ManagedProcess, ProcessSpec, SubprocessLauncher
from phono_console.policy import Route
from phono_console.router import ProcessAudioRouter
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


def test_subprocess_output_is_inherited(monkeypatch) -> None:
    async def scenario() -> None:
        captured: dict[str, object] = {}
        process = FakeProcess()

        async def fake_create(*argv, **kwargs):
            captured.update(kwargs)
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
        result = await SubprocessLauncher().start(("alsaloop", "-q"))
        assert result is process
        assert captured["stdin"] is asyncio.subprocess.DEVNULL
        assert captured["stdout"] is None
        assert captured["stderr"] is None

    asyncio.run(scenario())


def test_managed_process_backs_off_after_unexpected_exit() -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        events = SimulatedEventSink()
        process = ManagedProcess(ProcessSpec("loopback", ("fake",)), launcher, events)
        await process.start()
        launcher.processes[0].returncode = 2
        for _ in range(3):
            try:
                await process.start()
            except RuntimeError:
                pass
        assert len(launcher.processes) == 1
        assert process.health["status"] == "failed"
        assert [name for name, _ in events.events].count("audio_process_exited") == 1

    asyncio.run(scenario())


def test_router_reconciliation_restarts_dead_loopback_after_backoff() -> None:
    async def scenario() -> None:
        launcher = FakeLauncher()
        managed = ManagedProcess(
            ProcessSpec("loopback", ("fake",)), launcher, SimulatedEventSink()
        )
        router = ProcessAudioRouter(managed)
        await router.apply(Route.LOCAL_PHONO)
        launcher.processes[0].returncode = 7
        try:
            await router.reconcile(Route.LOCAL_PHONO)
        except RuntimeError:
            pass
        managed._next_retry_at = 0
        await router.reconcile(Route.LOCAL_PHONO)
        assert len(launcher.processes) == 2
        assert managed.running

    asyncio.run(scenario())
