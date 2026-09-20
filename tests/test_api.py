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


def test_dashboard_assets_are_public_but_live_data_is_authenticated() -> None:
    async def scenario() -> None:
        state = StateStore()
        client = TestClient(TestServer(ControlApi(state, "secret").application()))
        await client.start_server()
        try:
            response = await client.get("/")
            assert response.status == 200
            assert "Phono Console" in await response.text()
            response = await client.get("/assets/dashboard.css")
            assert response.status == 200
            assert "meter-track" in await response.text()
            response = await client.get("/assets/dashboard.js")
            assert response.status == 200
            assert 'api("/v1/status")' in await response.text()
            response = await client.get("/v1/status")
            assert response.status == 401
        finally:
            await client.close()

    asyncio.run(scenario())


def test_level_history_reset_invokes_capture_reset() -> None:
    async def scenario() -> None:
        state = StateStore()
        resets = 0

        def reset() -> None:
            nonlocal resets
            resets += 1

        api = ControlApi(state, None, level_reset_action=reset)
        client = TestClient(TestServer(api.application()))
        await client.start_server()
        try:
            response = await client.post("/v1/levels/reset")
            assert response.status == 200
            assert resets == 1
            assert state.events[-1].event == "input_level_history_reset"
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
            assert response.status == 202
            assert not state.whole_house_requested
            assert "not confirmed" in (await response.json())["warning"]
        finally:
            await client.close()

    asyncio.run(scenario())


def test_health_separates_liveness_from_audio_readiness() -> None:
    async def scenario() -> None:
        state = StateStore()
        client = TestClient(TestServer(ControlApi(state, None).application()))
        await client.start_server()
        try:
            response = await client.get("/health/live")
            assert response.status == 200
            assert (await response.json())["operational"] is False
            response = await client.get("/health/ready")
            assert response.status == 503
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


def test_api_controls_time_limited_bluetooth_pairing() -> None:
    async def scenario() -> None:
        calls = []

        async def opened() -> None:
            calls.append("open")

        async def closed() -> None:
            calls.append("close")

        api = ControlApi(
            StateStore(),
            None,
            pairing_open_action=opened,
            pairing_close_action=closed,
        )
        client = TestClient(TestServer(api.application()))
        await client.start_server()
        try:
            assert (
                await client.put(
                    "/v1/bluetooth/pairing", json={"enabled": True}
                )
            ).status == 200
            assert (
                await client.put(
                    "/v1/bluetooth/pairing", json={"enabled": False}
                )
            ).status == 200
            assert calls == ["open", "close"]
        finally:
            await client.close()

    asyncio.run(scenario())


def test_api_controls_local_only_mode() -> None:
    async def scenario() -> None:
        calls = []

        async def action(enabled: bool) -> None:
            calls.append(enabled)

        state = StateStore()
        api = ControlApi(state, None, local_only_action=action)
        client = TestClient(TestServer(api.application()))
        await client.start_server()
        try:
            response = await client.put("/v1/local-only", json={"enabled": True})
            assert response.status == 200
            assert calls == [True]
            assert state.snapshot()["local_playback_only"] is True
        finally:
            await client.close()

    asyncio.run(scenario())


def test_api_controls_sticky_phono_output_mode() -> None:
    async def scenario() -> None:
        calls = []

        async def action(mode) -> None:
            calls.append(mode.value)

        state = StateStore()
        api = ControlApi(state, None, phono_output_action=action)
        client = TestClient(TestServer(api.application()))
        await client.start_server()
        try:
            response = await client.put(
                "/v1/phono-output", json={"mode": "downstairs"}
            )
            assert response.status == 200
            assert calls == ["downstairs"]
            assert state.snapshot()["phono_output_mode"] == "downstairs"
        finally:
            await client.close()

    asyncio.run(scenario())


def test_api_rolls_back_phono_mode_when_handoff_fails() -> None:
    async def scenario() -> None:
        async def action(_mode) -> None:
            raise RuntimeError("MA unavailable")

        state = StateStore()
        api = ControlApi(state, None, phono_output_action=action)
        client = TestClient(TestServer(api.application()))
        await client.start_server()
        try:
            response = await client.put(
                "/v1/phono-output", json={"mode": "downstairs"}
            )
            assert response.status == 503
            assert state.snapshot()["phono_output_mode"] == "local"
        finally:
            await client.close()

    asyncio.run(scenario())


def test_api_controls_bluetooth_media() -> None:
    async def scenario() -> None:
        calls: list[str] = []

        async def action(command: str) -> None:
            calls.append(command)

        api = ControlApi(StateStore(), None, bluetooth_media_action=action)
        client = TestClient(TestServer(api.application()))
        await client.start_server()
        try:
            response = await client.post(
                "/v1/bluetooth/media", json={"command": "next"}
            )
            assert response.status == 200
            assert calls == ["next"]
            rejected = await client.post(
                "/v1/bluetooth/media", json={"command": "volume-up"}
            )
            assert rejected.status == 400
        finally:
            await client.close()

    asyncio.run(scenario())


def test_api_controls_cec_amplifier_volume() -> None:
    async def scenario() -> None:
        calls: list[tuple[int, str]] = []

        async def action(volume: int, source: str) -> tuple[int, bool]:
            calls.append((volume, source))
            return volume, False

        api = ControlApi(StateStore(), None, amplifier_volume_action=action)
        client = TestClient(TestServer(api.application()))
        await client.start_server()
        try:
            response = await client.put(
                "/v1/amplifier/volume", json={"volume": 28}
            )
            assert response.status == 200
            assert await response.json() == {"volume": 28, "muted": False}
            assert calls == [(28, "direct")]
            response = await client.put(
                "/v1/amplifier/volume",
                json={"volume": 42},
                headers={"X-Phono-Volume-Source": "music_assistant"},
            )
            assert response.status == 200
            assert calls[-1] == (42, "music_assistant")
        finally:
            await client.close()

    asyncio.run(scenario())
