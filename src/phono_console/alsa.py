from __future__ import annotations

import asyncio

from .events import LoggingEventSink
from .interfaces import EventSink
from .pcm import rms_dbfs_s16le


class CaptureUnavailable(RuntimeError):
    pass


class ArecordLevelMonitor:
    """Continuously sample a 16-bit ALSA capture endpoint using arecord."""

    def __init__(
        self,
        device: str,
        events: EventSink | None = None,
        *,
        sample_rate: int = 48_000,
        channels: int = 2,
        window_ms: int = 100,
    ) -> None:
        self.device = device
        self.events = events or LoggingEventSink()
        self.sample_rate = sample_rate
        self.channels = channels
        self.window_ms = window_ms
        self._process: asyncio.subprocess.Process | None = None
        frames = max(1, sample_rate * window_ms // 1000)
        self._window_bytes = frames * channels * 2

    async def _start(self) -> None:
        try:
            self._process = await asyncio.create_subprocess_exec(
                "arecord",
                "-q",
                "-D",
                self.device,
                "-t",
                "raw",
                "-f",
                "S16_LE",
                "-r",
                str(self.sample_rate),
                "-c",
                str(self.channels),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except (FileNotFoundError, OSError) as exc:
            await self.events.emit(
                "capture_start_failed", {"device": self.device, "error": str(exc)}
            )
            raise CaptureUnavailable(str(exc)) from exc
        await self.events.emit("capture_started", {"device": self.device})

    async def level_dbfs(self) -> float:
        if self._process is None or self._process.returncode is not None:
            if self._process is not None:
                await self.events.emit(
                    "capture_exited",
                    {"device": self.device, "returncode": self._process.returncode},
                )
            await self._start()
        assert self._process is not None and self._process.stdout is not None
        try:
            pcm = await self._process.stdout.readexactly(self._window_bytes)
        except asyncio.IncompleteReadError as exc:
            returncode = await self._process.wait()
            self._process = None
            await self.events.emit(
                "capture_lost",
                {"device": self.device, "returncode": returncode},
            )
            raise CaptureUnavailable("ALSA capture ended unexpectedly") from exc
        return rms_dbfs_s16le(pcm)

    async def close(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            process.kill()
            await process.wait()
        await self.events.emit("capture_stopped", {"device": self.device})

