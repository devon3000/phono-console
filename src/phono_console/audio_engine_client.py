from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from .audio_engine_protocol import (
    HEADER_SIZE,
    AudioSource,
    FrameFlags,
    ProtocolError,
    TimestampedPcm,
)

MAX_PACKET_BYTES = HEADER_SIZE + 48_000 * 2 * 2


@dataclass
class SourceMetrics:
    received: int = 0
    dropped: int = 0
    discontinuities: int = 0
    sequence_gaps: int = 0
    last_sequence: int | None = None
    last_epoch: int | None = None
    last_timestamp_us: int | None = None


@dataclass
class FrameFanout:
    queue_frames: int
    queues: dict[AudioSource, asyncio.Queue[TimestampedPcm]] = field(
        init=False
    )
    metrics: dict[AudioSource, SourceMetrics] = field(init=False)

    def __post_init__(self) -> None:
        if self.queue_frames < 2:
            raise ValueError("queue_frames must be at least 2")
        self.queues = {
            source: asyncio.Queue(maxsize=self.queue_frames)
            for source in AudioSource
        }
        self.metrics = {source: SourceMetrics() for source in AudioSource}

    def publish(self, frame: TimestampedPcm) -> None:
        metric = self.metrics[frame.source]
        new_epoch = metric.last_epoch is not None and frame.epoch != metric.last_epoch
        if frame.flags & FrameFlags.DISCONTINUITY or new_epoch:
            metric.discontinuities += 1
        elif (
            metric.last_sequence is not None
            and frame.sequence != metric.last_sequence + 1
        ):
            metric.sequence_gaps += 1
        metric.received += 1
        metric.last_sequence = frame.sequence
        metric.last_epoch = frame.epoch
        metric.last_timestamp_us = frame.first_sample_time_us

        queue = self.queues[frame.source]
        if queue.full():
            queue.get_nowait()
            metric.dropped += 1
        queue.put_nowait(frame)

    async def frames(self, source: AudioSource) -> AsyncIterator[TimestampedPcm]:
        queue = self.queues[source]
        while True:
            yield await queue.get()


class AudioEngineClient:
    def __init__(self, socket_path: str, queue_frames: int = 50) -> None:
        self.socket_path = socket_path
        self.fanout = FrameFanout(queue_frames)
        self.connected = False
        self.error: str | None = None

    async def run(self, stop: asyncio.Event) -> None:
        retry_seconds = 0.5
        while not stop.is_set():
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            connection.setblocking(False)
            try:
                await asyncio.get_running_loop().sock_connect(
                    connection, self.socket_path
                )
                self.connected = True
                self.error = None
                retry_seconds = 0.5
                while not stop.is_set():
                    packet = await asyncio.get_running_loop().sock_recv(
                        connection, MAX_PACKET_BYTES
                    )
                    if not packet:
                        raise ConnectionError("audio engine disconnected")
                    self.fanout.publish(TimestampedPcm.decode(packet))
            except asyncio.CancelledError:
                raise
            except (OSError, ConnectionError, ProtocolError) as exc:
                self.error = str(exc)
            finally:
                self.connected = False
                connection.close()
            try:
                await asyncio.wait_for(stop.wait(), timeout=retry_seconds)
            except TimeoutError:
                retry_seconds = min(retry_seconds * 2, 10.0)

    def frames(self, source: AudioSource) -> AsyncIterator[TimestampedPcm]:
        return self.fanout.frames(source)
