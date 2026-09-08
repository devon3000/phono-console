from __future__ import annotations

import asyncio
import time
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
        self._next_retry_at = 0.0
        self._retry_seconds = 0.5
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def health(self) -> dict[str, object]:
        return {
            "status": "ok" if self.running else "failed",
            "message": "running" if self.running else (self._last_error or "stopped"),
            "process": self.spec.name,
            "retry_in_seconds": round(
                max(0.0, self._next_retry_at - time.monotonic()), 2
            ),
        }

    async def start(self) -> None:
        if self.running:
            return
        if self._process is not None:
            returncode = self._process.returncode
            self._last_error = f"exited with status {returncode}"
            await self.events.emit(
                "audio_process_exited",
                {"process": self.spec.name, "returncode": returncode},
            )
            self._process = None
            self._next_retry_at = time.monotonic() + self._retry_seconds
            self._retry_seconds = min(self._retry_seconds * 2, 30.0)
        remaining = self._next_retry_at - time.monotonic()
        if remaining > 0:
            raise RuntimeError(
                f"{self.spec.name} unavailable; restart backoff active"
            )
        try:
            self._process = await self.launcher.start(self.spec.argv)
        except Exception as exc:
            self._last_error = str(exc)
            self._next_retry_at = time.monotonic() + self._retry_seconds
            self._retry_seconds = min(self._retry_seconds * 2, 30.0)
            await self.events.emit(
                "audio_process_start_failed",
                {"process": self.spec.name, "error": str(exc)},
            )
            raise
        self._last_error = None
        self._next_retry_at = 0.0
        self._retry_seconds = 0.5
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
        self._last_error = None
        self._next_retry_at = 0.0
        self._retry_seconds = 0.5
