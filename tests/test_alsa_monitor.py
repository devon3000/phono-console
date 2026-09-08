import asyncio
import struct

import pytest

from phono_console.alsa import ArecordLevelMonitor, CaptureUnavailable
from phono_console.simulation import SimulatedEventSink


class FakeReader:
    def __init__(self, *, pcm: bytes | None = None, error: bytes = b"") -> None:
        self.pcm = pcm
        self.error = error

    async def readexactly(self, expected: int) -> bytes:
        if self.pcm is None:
            raise asyncio.IncompleteReadError(b"", expected)
        assert len(self.pcm) == expected
        return self.pcm

    async def read(self, _limit: int) -> bytes:
        return self.error


class FakeCaptureProcess:
    def __init__(self, reader: FakeReader, returncode: int | None = None) -> None:
        self.stdout = reader
        self.stderr = reader
        self.returncode = returncode
        self.killed = False

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 2
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_capture_failure_backs_off_then_reports_recovery(monkeypatch) -> None:
    async def scenario() -> None:
        failed = FakeCaptureProcess(FakeReader(error=b"device busy"), returncode=2)
        pcm = struct.pack("<2h", 1000, -2000)
        recovered = FakeCaptureProcess(FakeReader(pcm=pcm))
        processes = [failed, recovered]
        launches = 0

        async def create_subprocess(*_argv, **_kwargs):
            nonlocal launches
            process = processes[launches]
            launches += 1
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)
        events = SimulatedEventSink()
        monitor = ArecordLevelMonitor(
            "test", events, sample_rate=100, channels=2, window_ms=10
        )

        with pytest.raises(CaptureUnavailable, match="device busy"):
            await monitor.level_dbfs()
        with pytest.raises(CaptureUnavailable, match="retrying"):
            await monitor.level_dbfs()
        assert launches == 1

        monitor._next_retry_at = 0
        await monitor.level_dbfs()
        assert launches == 2
        assert monitor.health["status"] == "ok"
        assert [name for name, _ in events.events] == [
            "capture_lost",
            "capture_recovered",
        ]
        await monitor.close()

    asyncio.run(scenario())
