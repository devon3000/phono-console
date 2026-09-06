from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable

from aiohttp import web

from .state import StateStore

WholeHouseAction = Callable[[bool], Awaitable[None]]


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
    ) -> None:
        self.state = state
        self.token = token
        self.whole_house_available = whole_house_available
        self.whole_house_action = whole_house_action

    @web.middleware
    async def authenticate(self, request: web.Request, handler):
        if self.token is not None:
            supplied = request.headers.get("Authorization", "")
            expected = f"Bearer {self.token}"
            if not hmac.compare_digest(supplied, expected):
                raise web.HTTPUnauthorized()
        return await handler(request)

    async def health(self, _request: web.Request) -> web.Response:
        status = self.state.status
        return web.json_response(
            {
                "ok": status is not None,
                "route": status.route.value if status else None,
            },
            status=200 if status is not None else 503,
        )

    async def get_status(self, _request: web.Request) -> web.Response:
        return web.json_response(self.state.snapshot())

    async def set_whole_house(self, request: web.Request) -> web.Response:
        body = await request.json()
        requested = body.get("enabled")
        if not isinstance(requested, bool):
            raise web.HTTPBadRequest(text="enabled must be a boolean")
        if requested and not self.whole_house_available:
            raise web.HTTPConflict(
                text=(
                    "whole-house vinyl is unavailable until Sendspin source "
                    "support is enabled"
                )
            )
        if self.whole_house_action is not None:
            try:
                await self.whole_house_action(requested)
            except WholeHouseError as exc:
                raise web.HTTPConflict(text=str(exc)) from exc
            except Exception as exc:
                raise web.HTTPBadGateway(
                    text=f"whole-house request failed: {exc}"
                ) from exc
        await self.state.request_whole_house(requested)
        await self.state.emit(
            "whole_house_request_changed", {"enabled": requested, "source": "api"}
        )
        return web.json_response({"enabled": requested})

    def application(self) -> web.Application:
        app = web.Application(middlewares=[self.authenticate])
        app.add_routes(
            [
                web.get("/health", self.health),
                web.get("/v1/status", self.get_status),
                web.put("/v1/whole-house", self.set_whole_house),
            ]
        )
        return app
