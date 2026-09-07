import asyncio
import struct

from phono_console.controller import Status
from phono_console.levels import LevelSession, analyze_s16le_stereo
from phono_console.policy import Route
from phono_console.state import StateStore


def test_state_snapshot_is_json_serializable_shape() -> None:
    async def scenario() -> None:
        store = StateStore(max_events=2)
        await store.set_status(Status(Route.LOCAL_PHONO, True, False, False, -20.0))
        await store.emit("one", {})
        await store.emit("two", {})
        await store.emit("three", {})
        snapshot = store.snapshot()
        assert snapshot["status"]["route"] == "local_phono"
        assert [event["event"] for event in snapshot["events"]] == ["two", "three"]

    asyncio.run(scenario())


def test_state_snapshot_includes_live_stereo_levels_and_resettable_history() -> None:
    async def scenario() -> None:
        store = StateStore()
        level = analyze_s16le_stereo(struct.pack("<4h", 32767, 4096, 1000, -4096))
        session = LevelSession()
        session.update(level)
        await store.set_input_levels(level, session)

        snapshot = store.snapshot()
        assert snapshot["input_levels"]["left"]["peak_dbfs"] == 0.0
        assert snapshot["input_levels"]["left"]["clipped"] is True
        assert snapshot["input_levels"]["right"]["max_peak_dbfs"] < 0

        session.reset()
        await store.reset_input_level_history()
        snapshot = store.snapshot()
        assert snapshot["input_levels"]["left"]["max_peak_dbfs"] == -120.0
        assert snapshot["input_levels"]["left"]["clipped"] is False

    asyncio.run(scenario())
