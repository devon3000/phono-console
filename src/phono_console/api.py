from __future__ import annotations

import asyncio
import hmac
from collections.abc import Awaitable, Callable
from importlib.resources import files

from aiohttp import web

from .state import StateStore
from .policy import OutputTarget, PhonoOutputMode

WholeHouseAction = Callable[[bool], Awaitable[None]]
LevelResetAction = Callable[[], None]
PairingAction = Callable[[], Awaitable[None]]
BluetoothDeviceAction = Callable[[str, str], Awaitable[None]]
BluetoothMediaAction = Callable[[str], Awaitable[None]]
AmplifierVolumeAction = Callable[[int, str], Awaitable[tuple[int, bool]]]
LocalOnlyAction = Callable[[bool], Awaitable[None]]
PhonoOutputAction = Callable[[PhonoOutputMode], Awaitable[None]]
OutputModeAction = Callable[[PhonoOutputMode], Awaitable[None]]
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
        output_mode_action: OutputModeAction | None = None,
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
        self.output_mode_action = output_mode_action
        self._output_mode_lock = asyncio.Lock()

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
        source = request.headers.get("X-Phono-Volume-Source", "direct")
        actual, muted = await self.amplifier_volume_action(volume, source)
        return web.json_response({"volume": actual, "muted": muted})

    async def set_local_only(self, request: web.Request) -> web.Response:
        body = await request.json()
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise web.HTTPBadRequest(text="enabled must be a boolean")
        mode = PhonoOutputMode.LOCAL if enabled else PhonoOutputMode.DOWNSTAIRS
        await self._set_canonical_output(mode, source="api_local_only")
        return web.json_response({"enabled": enabled})

    async def set_phono_output(self, request: web.Request) -> web.Response:
        body = await request.json()
        try:
            mode = PhonoOutputMode(body.get("mode"))
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(
                text="mode must be local or downstairs"
            ) from exc
        await self._set_canonical_output(mode, source="api_phono_output")
        return web.json_response({"mode": mode.value})

    async def set_output_mode(self, request: web.Request) -> web.Response:
        """Atomically select the console or Downstairs output path.

        This is deliberately one operation for both phono and Bluetooth.  The
        older dashboard issued separate local-only and phono-mode requests,
        allowing the controller to observe (and act on) an intermediate,
        contradictory state.
        """
        body = await request.json()
        try:
            mode = PhonoOutputMode(body.get("mode"))
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(
                text="mode must be local or downstairs"
            ) from exc

        await self._set_canonical_output(mode, source="api")
        local_only = mode is PhonoOutputMode.LOCAL
        return web.json_response(
            {"mode": mode.value, "local_playback_only": local_only}
        )

    async def set_whole_house(self, request: web.Request) -> web.Response:
        body = await request.json()
        requested = body.get("enabled")
        if not isinstance(requested, bool):
            raise web.HTTPBadRequest(text="enabled must be a boolean")
        mode = (
            PhonoOutputMode.DOWNSTAIRS
            if requested
            else PhonoOutputMode.LOCAL
        )
        warning = await self._set_canonical_output(
            mode, source="api_whole_house"
        )
        payload: dict[str, object] = {"enabled": requested}
        if warning is not None:
            payload["warning"] = warning
        return web.json_response(payload, status=202 if warning else 200)

    async def _set_canonical_output(
        self, mode: PhonoOutputMode, *, source: str
    ) -> str | None:
        """Map every public control onto one serialized output intent."""
        previous = self.state.output_target
        target = (
            OutputTarget.DOWNSTAIRS
            if mode is PhonoOutputMode.DOWNSTAIRS
            else OutputTarget.CONSOLE
        )
        if target is OutputTarget.DOWNSTAIRS and not self.whole_house_available:
            raise web.HTTPConflict(
                text="Downstairs is unavailable until Sendspin source support is enabled"
            )
        generation = await self.state.set_output_target(target)
        async with self._output_mode_lock:
            if generation != self.state.routing_generation:
                return None
            try:
                if self.output_mode_action is not None:
                    await self.output_mode_action(mode)
                elif self.phono_output_action is not None:
                    await self.phono_output_action(mode)
                elif self.local_only_action is not None:
                    await self.local_only_action(mode is PhonoOutputMode.LOCAL)
                elif self.whole_house_action is not None:
                    await self.whole_house_action(
                        mode is PhonoOutputMode.DOWNSTAIRS
                    )
            except WholeHouseError as exc:
                if target is OutputTarget.CONSOLE:
                    warning = f"remote stop was not confirmed: {exc}"
                    await self.state.emit(
                        "output_remote_stop_unconfirmed", {"error": str(exc)}
                    )
                    return warning
                if generation == self.state.routing_generation:
                    await self.state.set_output_target(previous, increment=False)
                raise web.HTTPConflict(text=str(exc)) from exc
            except Exception as exc:
                if target is OutputTarget.CONSOLE:
                    await self.state.emit(
                        "output_remote_stop_unconfirmed", {"error": str(exc)}
                    )
                else:
                    if generation == self.state.routing_generation:
                        await self.state.set_output_target(previous, increment=False)
                    raise web.HTTPServiceUnavailable(text=str(exc)) from exc
            await self.state.emit(
                "output_mode_changed",
                {
                    "mode": mode.value,
                    "requested_output": target.value,
                    "generation": generation,
                    "source": source,
                },
            )
            return None

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
                web.put("/v1/output-mode", self.set_output_mode),
                web.put("/v1/whole-house", self.set_whole_house),
            ]
        )
        return app
