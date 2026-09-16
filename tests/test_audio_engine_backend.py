import asyncio
from dataclasses import replace
from pathlib import Path

from phono_console.audio_engine_backend import TimestampedBluetoothBackend
from phono_console.audio_engine_protocol import (
    AudioSource,
    FrameFlags,
    TimestampedPcm,
)
from phono_console.config import load_config
from phono_console.policy import Source
from phono_console.state import StateStore


class FakeEvents:
    async def emit(self, event: str, details: dict[str, object]) -> None:
        pass


def frame() -> TimestampedPcm:
    return TimestampedPcm(
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


def test_backend_feeds_meter_and_sendspin_independently() -> None:
    async def scenario() -> None:
        config = load_config(
            Path(__file__).parents[1] / "config" / "phono-console.example.toml"
        )
        config = replace(
            config,
            bluetooth=replace(config.bluetooth, enabled=True),
            audio_engine=replace(config.audio_engine, backend="timestamped"),
        )
        backend = TimestampedBluetoothBackend.create(config, FakeEvents())
        sendspin_stream = backend.pcm_stream_factories[Source.BLUETOOTH]()
        meter_read = asyncio.create_task(backend.monitor.level_dbfs())
        sendspin_read = asyncio.create_task(anext(sendspin_stream))
        await asyncio.sleep(0)
        backend.client.fanout.publish(frame())

        assert await meter_read > -7.0
        assert (await sendspin_read).first_sample_time_us == 1_000_000
        assert backend.health["received_frames"] == 1
        await sendspin_stream.aclose()
        await backend.monitor.close()

    asyncio.run(scenario())


def test_backend_reports_engine_health_to_state() -> None:
    async def scenario() -> None:
        config = load_config(
            Path(__file__).parents[1] / "config" / "phono-console.example.toml"
        )
        backend = TimestampedBluetoothBackend.create(config, FakeEvents())
        state = StateStore()
        stop = asyncio.Event()
        run = asyncio.create_task(backend._report_health(stop, state))
        await asyncio.sleep(0.02)
        assert state.components["audio_engine"]["status"] == "degraded"
        assert "socket" in state.components["audio_engine"]
        stop.set()
        await run

    asyncio.run(scenario())
