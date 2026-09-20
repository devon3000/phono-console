from __future__ import annotations

import asyncio
import hmac
from collections.abc import Awaitable, Callable
from importlib.resources import files

from aiohttp import web

from .state import StateStore
from .policy import PhonoOutputMode

WholeHouseAction = Callable[[bool], Awaitable[None]]
LevelResetAction = Callable[[], None]
PairingAction = Callable[[], Awaitable[None]]
BluetoothDeviceAction = Callable[[str, str], Awaitable[None]]
BluetoothMediaAction = Callable[[str], Awaitable[None]]
AmplifierVolumeAction = Callable[[int], Awaitable[tuple[int, bool]]]
LocalOnlyAction = Callable[[bool], Awaitable[None]]
PhonoOutputAction = Callable[[PhonoOutputMode], Awaitable[None]]
PUBLIC_PATHS = frozenset(("/", "/assets/dashboard.css", "/assets/dashboard.js"))


class WholeHouseError(RuntimeError):
    """A whole-house request that cannot be satisfied right now (HTTP 409)."""


class ControlApi:
    def __init__(
        self,
        state: StateStore,
        token: str | None,
        *,
        whole_house_available: bool = True,
        whole_house_action: WholeHouseAction | None = None,
        level_reset_action: LevelResetAction | None = None,
        pairing_open_action: PairingAction | None = None,
        pairing_close_action: PairingAction | None = None,
        bluetooth_device_action: BluetoothDeviceAction | None = None,
        bluetooth_media_action: BluetoothMediaAction | None = None,
        amplifier_volume_action: AmplifierVolumeAction | None = None,
        local_only_action: LocalOnlyAction | None = None,
        phono_output_action: PhonoOutputAction | None = None,
    ) -> None:
        self.state = state
        self.token = token
        self.whole_house_available = whole_house_available
        self.whole_house_action = whole_house_action
        self.level_reset_action = level_reset_action
        self.pairing_open_action = pairing_open_action
        self.pairing_close_action = pairing_close_action
        self.bluetooth_device_action = bluetooth_device_action
        self.bluetooth_media_action = bluetooth_media_action
        self.amplifier_volume_action = amplifier_volume_action
        self.local_only_action = local_only_action
        self.phono_output_action = phono_output_action
        self._whole_house_lock = asyncio.Lock()

    @web.middleware
    async def authenticate(self, request: web.Request, handler):
        if self.token is not None and request.path not in PUBLIC_PATHS:
            supplied = request.headers.get("Authorization", "")
            expected = f"Bearer {self.token}"
            if not hmac.compare_digest(supplied, expected):
                raise web.HTTPUnauthorized()
        return await handler(request)

    async def health(self, _request: web.Request) -> web.Response:
        health = self.state.health_snapshot()
        status = self.state.status
        return web.json_response(
            {
                **health,
                "ok": health["operational"],
                "route": status.route.value if status else None,
            }
        )

    async def ready(self, _request: web.Request) -> web.Response:
        health = self.state.health_snapshot()
        return web.json_response(
            health, status=200 if health["operational"] else 503
        )

    async def get_status(self, _request: web.Request) -> web.Response:
        return web.json_response(self.state.snapshot())

    async def dashboard(self, _request: web.Request) -> web.Response:
        return self._asset_response("dashboard.html", "text/html")

    async def dashboard_css(self, _request: web.Request) -> web.Response:
        return self._asset_response("dashboard.css", "text/css")

    async def dashboard_js(self, _request: web.Request) -> web.Response:
        return self._asset_response("dashboard.js", "text/javascript")

    @staticmethod
    def _asset_response(name: str, content_type: str) -> web.Response:
        content = files("phono_console").joinpath("static", name).read_text()
        return web.Response(
            text=content,
            content_type=content_type,
            headers={"Cache-Control": "no-store"},
        )

    async def reset_levels(self, _request: web.Request) -> web.Response:
        if self.level_reset_action is not None:
            self.level_reset_action()
        await self.state.reset_input_level_history()
        await self.state.emit("input_level_history_reset", {"source": "api"})
        return web.json_response({"reset": True})

    async def set_bluetooth_pairing(self, request: web.Request) -> web.Response:
        body = await request.json()
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise web.HTTPBadRequest(text="enabled must be a boolean")
        action = self.pairing_open_action if enabled else self.pairing_close_action
        if action is None:
            raise web.HTTPConflict(text="Bluetooth is disabled")
        try:
            await action()
        except Exception as exc:
            raise web.HTTPServiceUnavailable(text=str(exc)) from exc
        return web.json_response({"enabled": enabled})

    async def bluetooth_device(self, request: web.Request) -> web.Response:
        if self.bluetooth_device_action is None:
            raise web.HTTPConflict(text="Bluetooth is disabled")
        body = await request.json()
        address = body.get("address")
        action = body.get("action")
        if not isinstance(address, str) or action not in {"disconnect", "forget"}:
            raise web.HTTPBadRequest(text="address and disconnect/forget action required")
        try:
            await self.bluetooth_device_action(
                "remove" if action == "forget" else action, address
            )
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        except Exception as exc:
            raise web.HTTPServiceUnavailable(text=str(exc)) from exc
        return web.json_response({"address": address, "action": action})

    async def bluetooth_media(self, request: web.Request) -> web.Response:
        if self.bluetooth_media_action is None:
            raise web.HTTPConflict(text="Bluetooth media control is disabled")
        body = await request.json()
        command = body.get("command")
        if command not in {"play", "pause", "next", "previous"}:
            raise web.HTTPBadRequest(text="command must be play, pause, next, or previous")
        try:
            await self.bluetooth_media_action(command)
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        except Exception as exc:
            raise web.HTTPServiceUnavailable(text=str(exc)) from exc
        return web.json_response({"command": command})

    async def amplifier_volume(self, request: web.Request) -> web.Response:
        if self.amplifier_volume_action is None:
            raise web.HTTPConflict(text="CEC amplifier control is unavailable")
        body = await request.json()
        volume = body.get("volume")
        if not isinstance(volume, int) or not 0 <= volume <= 100:
            raise web.HTTPBadRequest(text="volume must be an integer from 0 to 100")
        actual, muted = await self.amplifier_volume_action(volume)
        return web.json_response({"volume": actual, "muted": muted})

    async def set_local_only(self, request: web.Request) -> web.Response:
        body = await request.json()
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise web.HTTPBadRequest(text="enabled must be a boolean")
        if self.local_only_action is not None:
            try:
                await self.local_only_action(enabled)
            except Exception as exc:
                raise web.HTTPServiceUnavailable(text=str(exc)) from exc
        await self.state.set_local_playback_only(enabled)
        await self.state.emit(
            "local_playback_only_changed", {"enabled": enabled, "source": "api"}
        )
        return web.json_response({"enabled": enabled})

    async def set_phono_output(self, request: web.Request) -> web.Response:
        body = await request.json()
        try:
            mode = PhonoOutputMode(body.get("mode"))
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(
                text="mode must be local or downstairs"
            ) from exc
        previous_mode = self.state.phono_output_mode
        # Publish the requested mode before the remote handoff begins. The
        # controller keeps direct phono playback active until MA confirms the
        # buffered return feed, then swaps the console output to that feed.
        await self.state.set_phono_output_mode(mode)
        if self.phono_output_action is not None:
            try:
                await self.phono_output_action(mode)
            except WholeHouseError as exc:
                await self.state.set_phono_output_mode(previous_mode)
                raise web.HTTPConflict(text=str(exc)) from exc
            except Exception as exc:
                await self.state.set_phono_output_mode(previous_mode)
                raise web.HTTPServiceUnavailable(text=str(exc)) from exc
        await self.state.emit(
            "phono_output_mode_changed", {"mode": mode.value, "source": "api"}
        )
        return web.json_response({"mode": mode.value})

    async def set_whole_house(self, request: web.Request) -> web.Response:
        body = await request.json()
        requested = body.get("enabled")
        if not isinstance(requested, bool):
            raise web.HTTPBadRequest(text="enabled must be a boolean")
        async with self._whole_house_lock:
            if requested and not self.whole_house_available:
                raise web.HTTPConflict(
                    text=(
                        "whole-house vinyl is unavailable until Sendspin source "
                        "support is enabled"
                    )
                )

            # Clearing the local request is fail-safe and must not depend on a
            # remote server. It immediately releases local routing even if MA
            # is offline; the response still reports that the remote stop was
            # not confirmed.
            if not requested:
                await self.state.request_whole_house(False)
                await self.state.emit(
                    "whole_house_request_changed",
                    {"enabled": False, "source": "api"},
                )
                if self.whole_house_action is not None:
                    try:
                        async with asyncio.timeout(10):
                            await self.whole_house_action(False)
                    except Exception as exc:
                        await self.state.emit(
                            "whole_house_remote_stop_unconfirmed",
                            {"error": str(exc)},
                        )
                        return web.json_response(
                            {
                                "enabled": False,
                                "warning": f"remote stop was not confirmed: {exc}",
                            },
                            status=202,
                        )
                return web.json_response({"enabled": False})

            if self.whole_house_action is not None:
                try:
                    async with asyncio.timeout(10):
                        await self.whole_house_action(True)
                except WholeHouseError as exc:
                    raise web.HTTPConflict(text=str(exc)) from exc
                except Exception as exc:
                    raise web.HTTPBadGateway(
                        text=f"whole-house request failed: {exc}"
                    ) from exc
            await self.state.request_whole_house(True)
            await self.state.emit(
                "whole_house_request_changed", {"enabled": True, "source": "api"}
            )
            return web.json_response({"enabled": True})

    def application(self) -> web.Application:
        app = web.Application(middlewares=[self.authenticate])
        app.add_routes(
            [
                web.get("/", self.dashboard),
                web.get("/assets/dashboard.css", self.dashboard_css),
                web.get("/assets/dashboard.js", self.dashboard_js),
                web.get("/health", self.health),
                web.get("/health/live", self.health),
                web.get("/health/ready", self.ready),
                web.get("/v1/status", self.get_status),
                web.post("/v1/levels/reset", self.reset_levels),
                web.put("/v1/bluetooth/pairing", self.set_bluetooth_pairing),
                web.post("/v1/bluetooth/device", self.bluetooth_device),
                web.post("/v1/bluetooth/media", self.bluetooth_media),
                web.put("/v1/amplifier/volume", self.amplifier_volume),
                web.put("/v1/local-only", self.set_local_only),
                web.put("/v1/phono-output", self.set_phono_output),
                web.put("/v1/whole-house", self.set_whole_house),
            ]
        )
        return app
