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
    """Render engine PCM through ALSA with bounded output backpressure."""

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
        write_timeout_seconds: float = 2.0,
    ) -> None:
        self._frames = frames
        self.playback_device = playback_device
        self.sample_rate = sample_rate
        self.channels = channels
        self.events = events
        self.max_soft_correction_ppm = max_soft_correction_ppm
        self._process_factory = process_factory or self._start_aplay
        self.write_timeout_seconds = write_timeout_seconds
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

    async def _start_aplay(self) -> PlaybackProcess:
        # Engine PCM is already at the output rate. aplay supplies ALSA xrun
        # recovery without a second resampling stage. Start after 100 ms of
        # queued audio, with 250 ms of hardware buffering for arrival jitter.
        return await asyncio.create_subprocess_exec(
            "aplay",
            "-q",
            "-D",
            self.playback_device,
            "-t",
            "raw",
            "-f",
            "S16_LE",
            "-r",
            str(self.sample_rate),
            "-c",
            str(self.channels),
            "--buffer-time=250000",
            "--period-time=20000",
            "--start-delay=100000",
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
            "backend": "aplay",
            "uptime_seconds": (
                None
                if self._started_at is None
                else round(time.monotonic() - self._started_at, 2)
            ),
        }

    async def start(self) -> None:
        if self.running:
            return
        # Reconcile replaces a dead/stalled output without abandoning its child.
        if self._pump_task is not None or self._process is not None:
            await self.stop()
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
                try:
                    await asyncio.wait_for(
                        process.stdin.drain(), timeout=self.write_timeout_seconds
                    )
                except TimeoutError as exc:
                    raise RuntimeError("local ALSA output stopped accepting PCM") from exc
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
                await asyncio.wait_for(process.stdin.wait_closed(), timeout=0.5)
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
