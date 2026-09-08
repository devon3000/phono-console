from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress

from music_assistant_client import MusicAssistantClient
from music_assistant_models.enums import MediaType, PlaybackState
from music_assistant_models.helpers import create_uri

from .interfaces import EventSink
from .state import StateStore

# Provider domain of Music Assistant's sendspin_source plugin, which exposes
# source-role clients as playable audio sources addressed by client_id.
SENDSPIN_PROVIDER_DOMAIN = "sendspin_source"


class MusicAssistantState:
    """Maintain the MA websocket and expose console playback state."""

    def __init__(
        self,
        base_url: str,
        token: str | None,
        console_player: str,
        events: EventSink,
        state: StateStore | None = None,
    ) -> None:
        self.base_url = base_url
        self.token = token
        self.console_player = console_player
        self.events = events
        self.state = state
        self._client: MusicAssistantClient | None = None
        self._listener: asyncio.Task[None] | None = None
        self._ready: asyncio.Event | None = None
        self._whole_house_requested = False
        self._lock = asyncio.Lock()
        self._connection_task: asyncio.Task[None] | None = None
        self._next_retry_at = 0.0
        self._retry_seconds = 1.0
        self._last_console_playing = False
        self._player_missing_reported = False

    async def _publish_state(
        self, connected: bool, error: str | None = None
    ) -> None:
        if self.state is None:
            return
        payload: dict[str, object] = {
            "connected": connected,
            "server": self.base_url,
            "console_player": self.console_player,
        }
        if error is not None:
            payload["error"] = error
        await self.state.set_music_assistant_state(payload)
        await self.state.set_component(
            "music_assistant",
            "ok" if connected else "degraded",
            "connected" if connected else (error or "disconnected"),
            server=self.base_url,
            console_player=self.console_player,
        )

    async def _ensure_connected(self) -> None:
        async with self._lock:
            if self._listener is not None and not self._listener.done():
                if self._ready is not None:
                    await asyncio.wait_for(self._ready.wait(), timeout=10)
                return
            if self._listener is not None:
                error = None
                if not self._listener.cancelled():
                    with suppress(Exception):
                        error = str(self._listener.exception())
                await self.events.emit("ma_disconnected", {"error": error})
                await self._publish_state(False, error)
            self._client = MusicAssistantClient(self.base_url, None, self.token)
            self._ready = asyncio.Event()
            self._listener = asyncio.create_task(
                self._client.start_listening(self._ready),
                name="music-assistant-listener",
            )
            try:
                await asyncio.wait_for(self._ready.wait(), timeout=10)
            except Exception:
                self._listener.cancel()
                with suppress(asyncio.CancelledError):
                    await self._listener
                self._listener = None
                raise
            await self.events.emit("ma_connected", {"server": self.base_url})
            await self._publish_state(True)

    def _find_named_player(self, name: str):
        assert self._client is not None
        for player in self._client.players:
            if player.player_id == name or player.name == name:
                return player
        return None

    def _find_player(self):
        return self._find_named_player(self.console_player)

    async def _connect_for_status(self) -> None:
        try:
            await self._ensure_connected()
        except Exception as exc:
            await self.events.emit(
                "ma_connection_failed",
                {
                    "server": self.base_url,
                    "error": str(exc),
                    "retry_seconds": self._retry_seconds,
                },
            )
            await self._publish_state(False, str(exc))
            self._next_retry_at = (
                asyncio.get_running_loop().time() + self._retry_seconds
            )
            self._retry_seconds = min(self._retry_seconds * 2, 30.0)
            return
        self._next_retry_at = 0.0
        self._retry_seconds = 1.0

    async def console_is_playing(self) -> bool:
        task = self._connection_task
        if task is not None and task.done():
            # _connect_for_status handles and records expected connection failures.
            with suppress(Exception):
                task.result()
            self._connection_task = None

        ready = (
            self._listener is not None
            and not self._listener.done()
            and self._ready is not None
            and self._ready.is_set()
        )
        if not ready:
            now = asyncio.get_running_loop().time()
            if self._connection_task is None and now >= self._next_retry_at:
                self._connection_task = asyncio.create_task(
                    self._connect_for_status(), name="music-assistant-connect"
                )
            # If playback was active when telemetry disappeared, keep the
            # output reserved. Guessing "stopped" here can mix local phono
            # with a Sendspin player that is still rendering audio.
            if self.state is not None:
                await self.state.set_component(
                    "sendspin_player",
                    "degraded",
                    "playback state unavailable",
                    output_reserved=self._last_console_playing,
                )
            return self._last_console_playing

        player = self._find_player()
        if player is None:
            if not self._player_missing_reported:
                await self.events.emit(
                    "ma_player_missing", {"player": self.console_player}
                )
                self._player_missing_reported = True
            if self.state is not None:
                await self.state.set_component(
                    "sendspin_player",
                    "failed",
                    f"console player not found: {self.console_player}",
                )
            return self._last_console_playing
        self._player_missing_reported = False
        if not player.available:
            self._last_console_playing = False
            if self.state is not None:
                await self.state.set_component(
                    "sendspin_player",
                    "failed",
                    f"console player unavailable: {self.console_player}",
                )
            return False
        self._last_console_playing = (
            player.playback_state is PlaybackState.PLAYING
        )
        if self.state is not None:
            await self.state.set_component(
                "sendspin_player",
                "ok",
                "playing" if self._last_console_playing else "ready",
                player=self.console_player,
            )
        return self._last_console_playing

    async def play_vinyl_source(
        self, source_client_id: str, players: Sequence[str]
    ) -> list[str]:
        """Start the published vinyl source on the named players/groups."""
        await self._ensure_connected()
        assert self._client is not None
        uri = create_uri(
            MediaType.AUDIO_SOURCE, SENDSPIN_PROVIDER_DOMAIN, source_client_id
        )
        started: list[str] = []
        for name in players:
            player = self._find_named_player(name)
            if player is None:
                await self.events.emit("ma_player_missing", {"player": name})
                continue
            await self._client.player_queues.play_media(player.player_id, uri)
            started.append(player.player_id)
        return started

    async def stop_players(self, players: Sequence[str]) -> list[str]:
        """Stop playback on the named players/groups."""
        await self._ensure_connected()
        assert self._client is not None
        stopped: list[str] = []
        for name in players:
            player = self._find_named_player(name)
            if player is None:
                await self.events.emit("ma_player_missing", {"player": name})
                continue
            await self._client.player_queues.stop(player.player_id)
            stopped.append(player.player_id)
        return stopped

    async def whole_house_is_requested(self) -> bool:
        if self.state is not None:
            return self.state.whole_house_requested
        return self._whole_house_requested

    def request_whole_house(self, requested: bool) -> None:
        self._whole_house_requested = requested

    async def close(self) -> None:
        if self._connection_task is not None:
            self._connection_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._connection_task
            self._connection_task = None
        if self._client is not None:
            await self._client.disconnect()
        if self._listener is not None:
            self._listener.cancel()
            with suppress(asyncio.CancelledError):
                await self._listener
        self._listener = None
        await self._publish_state(False)
