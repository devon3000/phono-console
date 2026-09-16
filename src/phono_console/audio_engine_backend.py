from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .audio_engine_client import AudioEngineClient
from .audio_engine_monitor import TimestampedLevelMonitor
from .audio_engine_playback import TimestampedLocalPlayback
from .audio_engine_protocol import AudioSource
from .config import Config
from .interfaces import EventSink
from .policy import Source
from .sendspin_source import PcmStreamFactory
from .state import StateStore


@dataclass
class TimestampedBluetoothBackend:
    """Shared Bluetooth consumers backed by the native timestamped engine."""

    client: AudioEngineClient
    monitor: TimestampedLevelMonitor
    playback: TimestampedLocalPlayback
    replay_frames: int

    @classmethod
    def create(
        cls, config: Config, events: EventSink
    ) -> TimestampedBluetoothBackend:
        client = AudioEngineClient(
            config.audio_engine.socket_path,
            queue_frames=config.audio_engine.queue_frames,
        )
        monitor = TimestampedLevelMonitor(
            "audio-engine:bluetooth",
            lambda: client.frames(AudioSource.BLUETOOTH),
            events,
        )
        replay_ms = (
            config.bluetooth.attack_ms + 3 * config.runtime.poll_interval_ms
        )
        replay_frames = min(
            config.audio_engine.queue_frames,
            max(1, replay_ms // config.audio_engine.frame_ms),
        )
        playback = TimestampedLocalPlayback(
            lambda: client.frames(
                AudioSource.BLUETOOTH, replay_frames=replay_frames
            ),
            config.audio.playback_device,
            config.audio.sample_rate,
            config.audio.channels,
            events,
        )
        return cls(client, monitor, playback, replay_frames)

    @property
    def pcm_stream_factories(self) -> dict[Source, PcmStreamFactory]:
        return {
            Source.BLUETOOTH: lambda: self.client.frames(
                AudioSource.BLUETOOTH, replay_frames=self.replay_frames
            )
        }

    @property
    def health(self) -> dict[str, object]:
        metrics = self.client.fanout.metrics[AudioSource.BLUETOOTH]
        message = (
            "connected"
            if self.client.connected
            else (self.client.error or "waiting for native audio engine")
        )
        return {
            "status": "ok" if self.client.connected else "degraded",
            "message": message,
            "socket": self.client.socket_path,
            "received_frames": metrics.received,
            "dropped_frames": metrics.dropped,
            "sequence_gaps": metrics.sequence_gaps,
            "discontinuities": metrics.discontinuities,
            "last_epoch": metrics.last_epoch,
            "last_timestamp_us": metrics.last_timestamp_us,
        }

    async def _report_health(
        self, stop: asyncio.Event, state: StateStore
    ) -> None:
        while not stop.is_set():
            health = dict(self.health)
            status = str(health.pop("status"))
            message = str(health.pop("message"))
            await state.set_component("audio_engine", status, message, **health)
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except TimeoutError:
                pass

    async def run(self, stop: asyncio.Event, state: StateStore | None = None) -> None:
        if state is None:
            await self.client.run(stop)
            return
        await asyncio.gather(self.client.run(stop), self._report_health(stop, state))
