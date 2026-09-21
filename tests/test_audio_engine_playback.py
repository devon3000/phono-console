import asyncio

from phono_console.audio_engine_playback import TimestampedLocalPlayback
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


class FakeStdin:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.returncode: int | None = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        assert self.returncode is not None
        return self.returncode


def test_local_playback_feeds_pcm_and_stops_cleanly() -> None:
    async def scenario() -> None:
        queue: asyncio.Queue[TimestampedPcm] = asyncio.Queue()

        async def frames():
            while True:
                yield await queue.get()

        process = FakeProcess()

        async def start_process():
            return process

        events = FakeEvents()
        playback = TimestampedLocalPlayback(
            frames,
            "test_output",
            48_000,
            2,
            events,
            process_factory=start_process,
        )
        await playback.start()
        await queue.put(
            TimestampedPcm(
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
                pcm=b"\x01\x00\x01\x00\x02\x00\x02\x00",
            )
        )
        await asyncio.sleep(0.01)
        assert bytes(process.stdin.data) == b"\x01\x00\x01\x00\x02\x00\x02\x00"
        assert playback.running
        await playback.stop()
        assert process.stdin.closed
        assert not playback.running
        assert [event for event, _ in events.events] == [
            "audio_process_started",
            "audio_process_stopped",
        ]

    asyncio.run(scenario())


def test_local_playback_caps_async_correction_to_configured_ppm() -> None:
    playback = TimestampedLocalPlayback(
        lambda: None,  # type: ignore[arg-type]
        "test_output",
        48_000,
        2,
        FakeEvents(),
        max_soft_correction_ppm=250,
    )

    assert playback.max_soft_correction_ppm == 250
    assert playback.async_samples_per_second == 12
