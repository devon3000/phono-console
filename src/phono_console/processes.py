from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from .interfaces import EventSink


class ProcessHandle(Protocol):
    @property
    def returncode(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


class ProcessLauncher(Protocol):
    async def start(self, argv: tuple[str, ...]) -> ProcessHandle: ...


class SubprocessLauncher:
    async def start(self, argv: tuple[str, ...]) -> ProcessHandle:
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            # Inherit the service's output so systemd captures child diagnostics
            # without unread pipes eventually blocking the audio process.
            stdout=None,
            stderr=None,
        )


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    argv: tuple[str, ...]
    stop_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError(f"{self.name} command cannot be empty")


class ManagedProcess:
    def __init__(
        self,
        spec: ProcessSpec,
        launcher: ProcessLauncher,
        events: EventSink,
    ) -> None:
        self.spec = spec
        self.launcher = launcher
        self.events = events
        self._process: ProcessHandle | None = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        if self.running:
            return
        if self._process is not None:
            await self.events.emit(
                "audio_process_exited",
                {"process": self.spec.name, "returncode": self._process.returncode},
            )
        try:
            self._process = await self.launcher.start(self.spec.argv)
        except Exception as exc:
            await self.events.emit(
                "audio_process_start_failed",
                {"process": self.spec.name, "error": str(exc)},
            )
            raise
        await self.events.emit("audio_process_started", {"process": self.spec.name})

    async def stop(self) -> None:
        process = self._process
        if process is None:
            return
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(
                    process.wait(), timeout=self.spec.stop_timeout_seconds
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                await self.events.emit(
                    "audio_process_killed", {"process": self.spec.name}
                )
        await self.events.emit(
            "audio_process_stopped",
            {"process": self.spec.name, "returncode": process.returncode},
        )
        self._process = None
