from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag

MAGIC = 0x50434146  # PCAF
VERSION = 1
HEADER = struct.Struct("<IHHHHIQqIIHHiII")
HEADER_SIZE = HEADER.size


class ProtocolError(ValueError):
    pass


class AudioSource(IntEnum):
    PHONO = 1
    BLUETOOTH = 2
    MA_RETURN = 3


class FrameFlags(IntFlag):
    NONE = 0
    DISCONTINUITY = 1 << 0
    XRUN_RECOVERED = 1 << 1
    CLOCK_RESET = 1 << 2
    END_OF_STREAM = 1 << 3


@dataclass(frozen=True)
class TimestampedPcm:
    source: AudioSource
    flags: FrameFlags
    sequence: int
    first_sample_time_us: int
    source_rate_hz: int
    output_rate_hz: int
    channels: int
    frames: int
    reported_transport_delay_us: int
    epoch: int
    pcm: bytes

    def encode(self) -> bytes:
        if self.frames * self.channels * 2 != len(self.pcm):
            raise ProtocolError("PCM byte length does not match frames/channels")
        return HEADER.pack(
            MAGIC,
            VERSION,
            HEADER_SIZE,
            int(self.source),
            0,
            int(self.flags),
            self.sequence,
            self.first_sample_time_us,
            self.source_rate_hz,
            self.output_rate_hz,
            self.channels,
            self.frames,
            self.reported_transport_delay_us,
            self.epoch,
            len(self.pcm),
        ) + self.pcm

    @classmethod
    def decode(cls, packet: bytes) -> TimestampedPcm:
        if len(packet) < HEADER_SIZE:
            raise ProtocolError("truncated audio frame header")
        (
            magic,
            version,
            header_size,
            source,
            reserved,
            flags,
            sequence,
            first_sample_time_us,
            source_rate_hz,
            output_rate_hz,
            channels,
            frames,
            transport_delay_us,
            epoch,
            pcm_bytes,
        ) = HEADER.unpack_from(packet)
        if magic != MAGIC:
            raise ProtocolError("invalid audio frame magic")
        if version != VERSION or header_size != HEADER_SIZE:
            raise ProtocolError(
                f"unsupported audio protocol {version}/{header_size}"
            )
        if reserved != 0:
            raise ProtocolError("reserved audio frame field is nonzero")
        if len(packet) != HEADER_SIZE + pcm_bytes:
            raise ProtocolError("audio frame packet length mismatch")
        if frames * channels * 2 != pcm_bytes:
            raise ProtocolError("audio frame PCM shape mismatch")
        try:
            parsed_source = AudioSource(source)
            parsed_flags = FrameFlags(flags)
        except ValueError as exc:
            raise ProtocolError(str(exc)) from exc
        return cls(
            parsed_source,
            parsed_flags,
            sequence,
            first_sample_time_us,
            source_rate_hz,
            output_rate_hz,
            channels,
            frames,
            transport_delay_us,
            epoch,
            packet[HEADER_SIZE:],
        )
