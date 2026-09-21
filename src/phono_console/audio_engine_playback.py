from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Protocol

from .audio_engine_protocol import TimestampedPcm
from .interfaces import EventSink


class PlaybackProcess(Protocol):
    stdin: asyncio.StreamWriter | None

    @property
    def returncode(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


FrameStreamFactory = Callable[[], AsyncIterator[TimestampedPcm]]
PlaybackProcessFactory = Callable[[], Awaitable[PlaybackProcess]]


class TimestampedLocalPlayback:
    """Render engine PCM locally while adapting only to the output clock."""

    def __init__(
        self,
        frames: FrameStreamFactory,
        playback_device: str,
        sample_rate: int,
        channels: int,
        events: EventSink,
        *,
        max_soft_correction_ppm: int = 250,
        process_factory: PlaybackProcessFactory | None = None,
    ) -> None:
        self._frames = frames
        self.playback_device = playback_device
        self.sample_rate = sample_rate
        self.channels = channels
        self.events = events
        self.max_soft_correction_ppm = max_soft_correction_ppm
        self._process_factory = process_factory or self._start_ffmpeg
        self._process: PlaybackProcess | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._last_error: str | None = None
        self._started_at: float | None = None

    @property
    def async_samples_per_second(self) -> int:
        return max(
            1,
            round(self.sample_rate * self.max_soft_correction_ppm / 1_000_000),
        )

    async def _start_ffmpeg(self) -> PlaybackProcess:
        # FFmpeg's ``async`` value is the maximum number of samples per second
        # that aresample may stretch or squeeze. The previous value of 1000 at
        # 48 kHz allowed about 2% pitch/speed modulation. Convert the intended
        # ppm clock-correction ceiling into samples/second instead (250 ppm at
        # 48 kHz is 12 samples/second).
        return await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "s16le",
            "-sample_rate",
            str(self.sample_rate),
            "-ac",
            str(self.channels),
            "-i",
            "pipe:0",
            "-af",
            (
                f"aresample={self.sample_rate}:"
                f"async={self.async_samples_per_second}"
            ),
            "-ar",
            str(self.sample_rate),
            "-ac",
            str(self.channels),
            "-f",
            "alsa",
            self.playback_device,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=None,
        )

    @property
    def running(self) -> bool:
        return bool(
            self._process is not None
            and self._process.returncode is None
            and self._pump_task is not None
            and not self._pump_task.done()
        )

    @property
    def health(self) -> dict[str, object]:
        return {
            "status": "ok" if self.running else "failed",
            "message": "running" if self.running else (self._last_error or "stopped"),
            "process": "timestamped_bluetooth_playback",
            "playback_device": self.playback_device,
            "uptime_seconds": (
                None
                if self._started_at is None
                else round(time.monotonic() - self._started_at, 2)
            ),
        }

    async def start(self) -> None:
        if self.running:
            return
        if self._pump_task is not None:
            with suppress(Exception):
                await self._pump_task
            self._pump_task = None
        if self._process is not None:
            returncode = self._process.returncode
            self._process = None
            raise RuntimeError(
                self._last_error
                or f"timestamped Bluetooth playback exited with status {returncode}"
            )
        self._process = await self._process_factory()
        if self._process.stdin is None:
            self._process = None
            raise RuntimeError("local playback process has no PCM input")
        self._last_error = None
        self._started_at = time.monotonic()
        self._pump_task = asyncio.create_task(self._pump())
        await self.events.emit(
            "audio_process_started",
            {"process": "timestamped_bluetooth_playback"},
        )

    async def _pump(self) -> None:
        stream = self._frames()
        try:
            async for frame in stream:
                if frame.output_rate_hz != self.sample_rate:
                    raise RuntimeError(
                        f"unexpected engine rate {frame.output_rate_hz}"
                    )
                if frame.channels != self.channels:
                    raise RuntimeError(
                        f"unexpected engine channel count {frame.channels}"
                    )
                process = self._process
                if process is None or process.stdin is None:
                    return
                process.stdin.write(frame.pcm)
                await process.stdin.drain()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = str(exc)
            await self.events.emit(
                "audio_process_failed",
                {
                    "process": "timestamped_bluetooth_playback",
                    "error": str(exc),
                },
            )
        finally:
            with suppress(Exception):
                await stream.aclose()

    async def stop(self) -> None:
        task = self._pump_task
        self._pump_task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        process = self._process
        self._process = None
        if process is None:
            self._started_at = None
            return
        if process.stdin is not None:
            with suppress(Exception):
                process.stdin.close()
                await process.stdin.wait_closed()
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        await self.events.emit(
            "audio_process_stopped",
            {
                "process": "timestamped_bluetooth_playback",
                "returncode": process.returncode,
            },
        )
        self._last_error = None
        self._started_at = None
