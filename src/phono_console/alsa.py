from __future__ import annotations

import asyncio
import time

from .events import LoggingEventSink
from .interfaces import EventSink
from .levels import LevelSession, StereoLevel, analyze_s16le_stereo
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
        self._next_retry_at = 0.0
        self._retry_seconds = 0.5
        self._last_error: str | None = None
        self._last_sample_at: float | None = None
        self.latest: StereoLevel | None = None
        self.session = LevelSession()
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
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            await self._record_failure("capture_start_failed", str(exc))
            raise CaptureUnavailable(str(exc)) from exc

    async def _record_failure(self, event: str, error: str, **details: object) -> None:
        now = time.monotonic()
        self._next_retry_at = now + self._retry_seconds
        retry = self._retry_seconds
        self._retry_seconds = min(self._retry_seconds * 2, 30.0)
        changed = error != self._last_error
        self._last_error = error
        if changed:
            await self.events.emit(
                event,
                {
                    "device": self.device,
                    "error": error,
                    "retry_seconds": retry,
                    **details,
                },
            )

    async def _stderr_text(self, process: asyncio.subprocess.Process) -> str:
        if process.stderr is None:
            return ""
        try:
            raw = await asyncio.wait_for(process.stderr.read(4096), timeout=0.25)
        except TimeoutError:
            return ""
        return raw.decode(errors="replace").strip()

    async def read_pcm(self) -> bytes:
        if self._process is None or self._process.returncode is not None:
            if self._process is not None:
                process = self._process
                returncode = process.returncode
                error = await self._stderr_text(process)
                self._process = None
                message = error or f"ALSA capture exited with status {returncode}"
                await self._record_failure(
                    "capture_lost", message, returncode=returncode
                )
            remaining = self._next_retry_at - time.monotonic()
            if remaining > 0:
                raise CaptureUnavailable(
                    f"ALSA capture unavailable; retrying in {remaining:.1f}s"
                )
            await self._start()
        assert self._process is not None and self._process.stdout is not None
        try:
            timeout = max(1.0, self.window_ms / 1000 * 5)
            pcm = await asyncio.wait_for(
                self._process.stdout.readexactly(self._window_bytes), timeout=timeout
            )
        except asyncio.IncompleteReadError as exc:
            process = self._process
            returncode = await process.wait()
            error = await self._stderr_text(process)
            self._process = None
            message = error or f"ALSA capture exited with status {returncode}"
            await self._record_failure(
                "capture_lost", message, returncode=returncode
            )
            raise CaptureUnavailable(message) from exc
        except TimeoutError as exc:
            process = self._process
            self._process = None
            if process.returncode is None:
                process.kill()
                await process.wait()
            error = "ALSA capture produced no audio before the read timeout"
            await self._record_failure("capture_stalled", error)
            raise CaptureUnavailable(error) from exc
        if self._last_error is not None:
            await self.events.emit("capture_recovered", {"device": self.device})
        elif self._last_sample_at is None:
            await self.events.emit("capture_started", {"device": self.device})
        self._last_error = None
        self._next_retry_at = 0.0
        self._retry_seconds = 0.5
        self._last_sample_at = time.monotonic()
        if self.channels == 2:
            self.latest = analyze_s16le_stereo(pcm)
            self.session.update(self.latest)
        return pcm

    @property
    def health(self) -> dict[str, object]:
        now = time.monotonic()
        return {
            "status": (
                "ok"
                if self._last_sample_at is not None and self._last_error is None
                else "failed"
            ),
            "message": self._last_error
            or (
                "capture is producing PCM"
                if self._last_sample_at is not None
                else "waiting for PCM capture"
            ),
            "device": self.device,
            "last_sample_age_seconds": (
                None
                if self._last_sample_at is None
                else round(now - self._last_sample_at, 2)
            ),
            "retry_in_seconds": round(max(0.0, self._next_retry_at - now), 2),
        }

    async def level_dbfs(self) -> float:
        pcm = await self.read_pcm()
        if self.latest is not None:
            return max(self.latest.left.rms_dbfs, self.latest.right.rms_dbfs)
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
