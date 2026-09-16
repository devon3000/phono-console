from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress

from .audio_engine_protocol import TimestampedPcm
from .interfaces import EventSink
from .levels import LevelSession, StereoLevel, analyze_s16le_stereo
from .pcm import rms_dbfs_s16le


class TimestampedLevelMonitor:
    """Measure source activity from the engine's shared PCM frame stream."""

    def __init__(
        self,
        device: str,
        frames: Callable[[], AsyncIterator[TimestampedPcm]],
        events: EventSink,
    ) -> None:
        self.device = device
        self._frame_factory = frames
        self._frames: AsyncIterator[TimestampedPcm] | None = None
        self.events = events
        self.latest: StereoLevel | None = None
        self.session = LevelSession()
        self._last_sample_at: float | None = None
        self._last_error: str | None = None

    async def level_dbfs(self) -> float:
        if self._frames is None:
            self._frames = self._frame_factory()
        try:
            frame = await anext(self._frames)
        except asyncio.CancelledError:
            stream = self._frames
            self._frames = None
            if stream is not None:
                with suppress(Exception):
                    await stream.aclose()
            raise
        except (StopAsyncIteration, OSError, RuntimeError) as exc:
            self._frames = None
            self._last_error = str(exc) or "audio engine stream ended"
            raise RuntimeError(self._last_error) from exc
        first = self._last_sample_at is None
        recovered = self._last_error is not None
        self._last_error = None
        self._last_sample_at = time.monotonic()
        if frame.channels == 2:
            self.latest = analyze_s16le_stereo(frame.pcm)
            self.session.update(self.latest)
            level = max(self.latest.left.rms_dbfs, self.latest.right.rms_dbfs)
        else:
            level = rms_dbfs_s16le(frame.pcm)
        if first:
            await self.events.emit("capture_started", {"device": self.device})
        elif recovered:
            await self.events.emit("capture_recovered", {"device": self.device})
        return level

    @property
    def health(self) -> dict[str, object]:
        return {
            "status": (
                "ok"
                if self._last_sample_at is not None and self._last_error is None
                else "failed"
            ),
            "message": self._last_error
            or (
                "audio engine is producing PCM"
                if self._last_sample_at is not None
                else "waiting for audio engine PCM"
            ),
            "device": self.device,
            "last_sample_age_seconds": (
                None
                if self._last_sample_at is None
                else round(time.monotonic() - self._last_sample_at, 2)
            ),
        }

    async def close(self) -> None:
        stream = self._frames
        self._frames = None
        if stream is not None:
            await stream.aclose()
        if self._last_sample_at is not None:
            await self.events.emit("capture_stopped", {"device": self.device})
