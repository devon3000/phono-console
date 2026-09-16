import asyncio

from phono_console.audio_engine_client import FrameFanout
from phono_console.audio_engine_protocol import (
    AudioSource,
    FrameFlags,
    TimestampedPcm,
)


def audio_frame(sequence: int, *, epoch: int = 1) -> TimestampedPcm:
    return TimestampedPcm(
        AudioSource.BLUETOOTH,
        FrameFlags.NONE,
        sequence,
        1_000_000 + sequence * 20_000,
        48_000,
        48_000,
        2,
        1,
        0,
        epoch,
        b"\0\0\0\0",
    )


def test_fanout_is_bounded_and_reports_drops_and_sequence_gaps() -> None:
    fanout = FrameFanout(queue_frames=2)
    fanout.publish(audio_frame(1))
    fanout.publish(audio_frame(3))
    fanout.publish(audio_frame(4))

    metric = fanout.metrics[AudioSource.BLUETOOTH]
    assert metric.received == 3
    assert metric.sequence_gaps == 1
    assert metric.dropped == 1
    assert fanout.queues[AudioSource.BLUETOOTH].qsize() == 2
    queued = fanout.queues[AudioSource.BLUETOOTH]
    assert (queued.get_nowait().flags & FrameFlags.DISCONTINUITY) == 0
    assert queued.get_nowait().flags & FrameFlags.DISCONTINUITY


def test_fanout_streams_each_source_independently() -> None:
    async def scenario() -> None:
        fanout = FrameFanout(queue_frames=2)
        fanout.publish(audio_frame(9))
        stream = fanout.frames(AudioSource.BLUETOOTH)
        assert (await anext(stream)).sequence == 9
        await stream.aclose()

    asyncio.run(scenario())


def test_fanout_delivers_one_frame_to_multiple_consumers() -> None:
    async def scenario() -> None:
        fanout = FrameFanout(queue_frames=2)
        first = fanout.frames(AudioSource.BLUETOOTH)
        second = fanout.frames(AudioSource.BLUETOOTH)
        first_read = asyncio.create_task(anext(first))
        second_read = asyncio.create_task(anext(second))
        await asyncio.sleep(0)
        fanout.publish(audio_frame(10))
        assert (await first_read).sequence == 10
        assert (await second_read).sequence == 10
        await first.aclose()
        await second.aclose()

    asyncio.run(scenario())


def test_new_secondary_consumer_can_replay_recent_frames() -> None:
    async def scenario() -> None:
        fanout = FrameFanout(queue_frames=4)
        primary = fanout.frames(AudioSource.BLUETOOTH)
        first_read = asyncio.create_task(anext(primary))
        await asyncio.sleep(0)
        fanout.publish(audio_frame(1))
        assert (await first_read).sequence == 1
        fanout.publish(audio_frame(2))
        fanout.publish(audio_frame(3))

        replay = fanout.frames(AudioSource.BLUETOOTH, replay_frames=2)
        assert (await anext(replay)).sequence == 2
        assert (await anext(replay)).sequence == 3
        await replay.aclose()
        await primary.aclose()

    asyncio.run(scenario())
