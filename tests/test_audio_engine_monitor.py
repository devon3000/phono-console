import asyncio

from phono_console.audio_engine_monitor import TimestampedLevelMonitor
from phono_console.audio_engine_protocol import (
    AudioSource,
    FrameFlags,
    TimestampedPcm,
)


class FakeEvents:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    async def emit(self, event: str, details: dict[str, object]) -> None:
        self.events.append((event, details))


def test_timestamped_level_monitor_measures_shared_frame() -> None:
    async def scenario() -> None:
        async def frames():
            yield TimestampedPcm(
                source=AudioSource.BLUETOOTH,
                flags=FrameFlags.NONE,
                sequence=1,
                first_sample_time_us=1_000_000,
                source_rate_hz=48_000,
                output_rate_hz=48_000,
                channels=2,
                frames=2,
                reported_transport_delay_us=0,
                epoch=1,
                pcm=b"\xff\x7f\xff\x7f\x00\x00\x00\x00",
            )

        events = FakeEvents()
        monitor = TimestampedLevelMonitor("engine:bluetooth", frames(), events)
        assert await monitor.level_dbfs() > -7.0
        assert monitor.health["status"] == "ok"
        assert events.events[0][0] == "capture_started"
        await monitor.close()

    asyncio.run(scenario())
