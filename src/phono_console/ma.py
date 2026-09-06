from __future__ import annotations

import asyncio
from contextlib import suppress

from music_assistant_client import MusicAssistantClient
from music_assistant_models.enums import PlaybackState

from .interfaces import EventSink


class MusicAssistantState:
    """Maintain the MA websocket and expose console playback state."""

    def __init__(
        self,
        base_url: str,
        token: str | None,
        console_player: str,
        events: EventSink,
    ) -> None:
        self.base_url = base_url
        self.token = token
        self.console_player = console_player
        self.events = events
        self._client: MusicAssistantClient | None = None
        self._listener: asyncio.Task[None] | None = None
        self._ready: asyncio.Event | None = None
        self._whole_house_requested = False
        self._lock = asyncio.Lock()

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
            self._client = MusicAssistantClient(self.base_url, None, self.token)
            self._ready = asyncio.Event()
            self._listener = asyncio.create_task(
                self._client.start_listening(self._ready), name="music-assistant-listener"
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

    def _find_player(self):
        assert self._client is not None
        for player in self._client.players:
            if player.player_id == self.console_player or player.name == self.console_player:
                return player
        return None

    async def console_is_playing(self) -> bool:
        await self._ensure_connected()
        player = self._find_player()
        if player is None:
            await self.events.emit(
                "ma_player_missing", {"player": self.console_player}
            )
            return False
        return player.available and player.playback_state is PlaybackState.PLAYING

    async def whole_house_is_requested(self) -> bool:
        return self._whole_house_requested

    def request_whole_house(self, requested: bool) -> None:
        self._whole_house_requested = requested

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
        if self._listener is not None:
            self._listener.cancel()
            with suppress(asyncio.CancelledError):
                await self._listener
        self._listener = None

