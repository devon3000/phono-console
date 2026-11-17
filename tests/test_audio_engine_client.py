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
    overrun = queued.get_nowait()
    assert overrun.flags & FrameFlags.DISCONTINUITY
    assert overrun.subscriber_overrun
    assert metric.idle_queue_drops == 1
    assert metric.active_subscriber_drops == 0


def test_fanout_streams_each_source_independently() -> None:
    async def scenario() -> None:
        fanout = FrameFanout(queue_frames=2)
        fanout.publish(audio_frame(9))
        stream = fanout.frames(AudioSource.BLUETOOTH)
        assert (await anext(stream)).sequence == 9
        await stream.aclose()

    asyncio.run(scenario())


def test_slow_subscriber_drops_do_not_change_other_consumers_or_wire_frame() -> None:
    async def scenario() -> None:
        fanout = FrameFanout(queue_frames=2)
        slow = fanout.frames(AudioSource.BLUETOOTH)
        fast = fanout.frames(AudioSource.BLUETOOTH)
        slow_read = asyncio.create_task(anext(slow))
        fast_read = asyncio.create_task(anext(fast))
        await asyncio.sleep(0)
        fanout.publish(audio_frame(1))
        await slow_read
        await fast_read
        for sequence in (2, 3, 4):
            fanout.publish(audio_frame(sequence))
            received = await anext(fast)
            assert received.flags == FrameFlags.NONE
            assert not received.subscriber_overrun
        assert (await anext(slow)).sequence == 3
        overrun = await anext(slow)
        assert overrun.sequence == 4
        assert overrun.subscriber_overrun
        assert fanout.metrics[AudioSource.BLUETOOTH].active_subscriber_drops == 1
        assert fanout.metrics[AudioSource.BLUETOOTH].idle_queue_drops == 0
        # Local queue provenance never changes the native packet layout.
        assert not TimestampedPcm.decode(overrun.encode()).subscriber_overrun
        await slow.aclose()
        await fast.aclose()

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
