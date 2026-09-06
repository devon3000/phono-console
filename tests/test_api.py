import asyncio

from aiohttp.test_utils import TestClient, TestServer

from phono_console.api import ControlApi
from phono_console.state import StateStore


def test_api_requires_token_and_controls_whole_house() -> None:
    async def scenario() -> None:
        state = StateStore()
        client = TestClient(TestServer(ControlApi(state, "secret").application()))
        await client.start_server()
        try:
            response = await client.get("/v1/status")
            assert response.status == 401
            headers = {"Authorization": "Bearer secret"}
            response = await client.put(
                "/v1/whole-house", json={"enabled": True}, headers=headers
            )
            assert response.status == 200
            assert state.whole_house_requested
            response = await client.get("/v1/status", headers=headers)
            payload = await response.json()
            assert payload["whole_house_requested"] is True
        finally:
            await client.close()

    asyncio.run(scenario())


def test_api_invokes_whole_house_action() -> None:
    async def scenario() -> None:
        from phono_console.api import WholeHouseError

        calls: list[bool] = []
        fail = False

        async def action(enabled: bool) -> None:
            if fail:
                raise WholeHouseError("players unavailable")
            calls.append(enabled)

        state = StateStore()
        api = ControlApi(state, None, whole_house_action=action)
        client = TestClient(TestServer(api.application()))
        await client.start_server()
        try:
            response = await client.put("/v1/whole-house", json={"enabled": True})
            assert response.status == 200
            assert calls == [True]
            assert state.whole_house_requested

            fail = True
            response = await client.put("/v1/whole-house", json={"enabled": False})
            assert response.status == 409
            assert state.whole_house_requested  # unchanged on failure
        finally:
            await client.close()

    asyncio.run(scenario())


def test_api_rejects_unavailable_whole_house_source() -> None:
    async def scenario() -> None:
        state = StateStore()
        api = ControlApi(state, None, whole_house_available=False)
        client = TestClient(TestServer(api.application()))
        await client.start_server()
        try:
            response = await client.put("/v1/whole-house", json={"enabled": True})
            assert response.status == 409
            assert not state.whole_house_requested
        finally:
            await client.close()

    asyncio.run(scenario())
