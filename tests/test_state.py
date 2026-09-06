import asyncio

from phono_console.controller import Status
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

