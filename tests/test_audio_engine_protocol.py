import pytest

from phono_console.audio_engine_protocol import (
    HEADER_SIZE,
    AudioSource,
    FrameFlags,
    ProtocolError,
    TimestampedPcm,
)


def frame() -> TimestampedPcm:
    return TimestampedPcm(
        source=AudioSource.BLUETOOTH,
        flags=FrameFlags.DISCONTINUITY,
        sequence=0x0102030405060708,
        first_sample_time_us=123456789,
        source_rate_hz=44_100,
        output_rate_hz=48_000,
        channels=2,
        frames=2,
        reported_transport_delay_us=175_000,
        epoch=7,
        pcm=b"\x01\x02\x03\x04\x05\x06\x07\x08",
    )


def test_timestamped_pcm_round_trip() -> None:
    encoded = frame().encode()
    assert len(encoded) == HEADER_SIZE + 8
    assert TimestampedPcm.decode(encoded) == frame()


def test_protocol_rejects_shape_and_truncation() -> None:
    invalid = frame().__class__(**{**frame().__dict__, "frames": 3})
    with pytest.raises(ProtocolError, match="length"):
        invalid.encode()
    with pytest.raises(ProtocolError, match="truncated"):
        TimestampedPcm.decode(b"PCAF")
    with pytest.raises(ProtocolError, match="packet length"):
        TimestampedPcm.decode(frame().encode()[:-1])
