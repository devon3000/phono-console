"""Publish the phono capture to Music Assistant through the Sendspin source role.

The publisher keeps one source-role connection to the configured Sendspin
server and streams the shared ALSA capture only while the server requests it
(``server/command`` ``source.start``/``source.stop``), which is how the
Music Assistant ``sendspin_source`` plugin drives playback. Line-sense signal
state is reported from the controller's phono-activity decision so Music
Assistant can offer autostart on a configured target group.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from pathlib import Path

from .config import AudioConfig, SendspinConfig
from .interfaces import EventSink
from .state import StateStore

LOGGER = logging.getLogger(__name__)

IDENTITY_FILE = "identity.key"
PAIRING_FILE = "pairing.json"
TIME_SYNC_TIMEOUT_SECONDS = 10.0

PcmStreamFactory = Callable[[], AsyncIterator[bytes]]
ClientFactory = Callable[[], Awaitable[object]]


async def arecord_pcm_stream(
    device: str, sample_rate: int, channels: int, chunk_frames: int
) -> AsyncIterator[bytes]:
    """Yield raw S16_LE PCM chunks from a dedicated arecord reader.

    The shared dsnoop capture device allows this reader to coexist with the
    level monitor and the local loopback.
    """
    process = await asyncio.create_subprocess_exec(
        "arecord",
        "-q",
        "-D",
        device,
        "-t",
        "raw",
        "-f",
        "S16_LE",
        "-r",
        str(sample_rate),
        "-c",
        str(channels),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert process.stdout is not None
    chunk_bytes = chunk_frames * channels * 2
    try:
        while True:
            yield await process.stdout.readexactly(chunk_bytes)
    except asyncio.IncompleteReadError:
        return
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()
                await process.wait()


def load_or_create_identity(state_dir: Path):
    """Load the persistent source identity, generating one on first run."""
    from aiosendspin.noise.keys import Identity

    state_dir.mkdir(parents=True, exist_ok=True)
    state_dir.chmod(0o700)
    identity_path = state_dir / IDENTITY_FILE
    if identity_path.exists():
        return Identity.from_private_bytes(identity_path.read_bytes())
    identity = Identity.generate()
    identity_path.touch(mode=0o600)
    identity_path.write_bytes(identity.private_bytes)
    return identity


class SendspinSourcePublisher:
    """Own the source-role client lifecycle: connect, pair, and stream on demand."""

    def __init__(
        self,
        sendspin: SendspinConfig,
        audio: AudioConfig,
        events: EventSink,
        state: StateStore,
        *,
        client_factory: ClientFactory | None = None,
        pcm_stream_factory: PcmStreamFactory | None = None,
        reconnect_seconds: float = 5.0,
        signal_poll_seconds: float = 0.5,
    ) -> None:
        self.config = sendspin
        self.audio = audio
        self.events = events
        self.state = state
        self.client_id: str | None = None
        self._client_factory = client_factory or self._default_client_factory
        self._pcm_stream_factory = pcm_stream_factory or self._default_pcm_stream
        self.reconnect_seconds = reconnect_seconds
        self.signal_poll_seconds = signal_poll_seconds
        self._client: object | None = None
        self._connected = False
        self._stream_task: asyncio.Task[None] | None = None
        self._streaming = False
        self._stream_lock = asyncio.Lock()

    def _default_pcm_stream(self) -> AsyncIterator[bytes]:
        # 20 ms chunks keep feed timestamps fine-grained without hammering
        # the websocket.
        chunk_frames = max(1, self.audio.sample_rate // 50)
        return arecord_pcm_stream(
            self.audio.capture_device,
            self.audio.sample_rate,
            self.audio.channels,
            chunk_frames,
        )

    async def _default_client_factory(self) -> object:
        from aiosendspin.client import PairingSupport, SendspinClient
        from aiosendspin.models.source import (
            ClientHelloSourceFeatures,
            ClientHelloSourceSupport,
        )
        from aiosendspin.models.types import Roles
        from aiosendspin.noise.trust_store import FileClientPairingStore

        state_dir = Path(self.config.state_dir)
        identity = await asyncio.to_thread(load_or_create_identity, state_dir)
        store = await FileClientPairingStore.open(state_dir / PAIRING_FILE)
        self.client_id = identity.peer_id
        return SendspinClient(
            identity,
            self.config.source_name,
            [Roles.SOURCE],
            pairing_store=store,
            source_support=ClientHelloSourceSupport(
                features=ClientHelloSourceFeatures(line_sense=True)
            ),
            pairing_support=PairingSupport(gesture_prompt=self._pairing_gesture),
        )

    async def _pairing_gesture(self, waiting: bool) -> None:
        # Headless device: accept any pairing attempt initiated from the
        # Music Assistant UI while it waits for the gesture. The trust store
        # persists the pairing so this happens once per server.
        if not waiting:
            return
        client = self._client
        if client is None:
            return
        opener = getattr(client, "open_pairing_window", None)
        if opener is not None:
            opener()
            await self.events.emit("sendspin_source_pairing_opened", {})

    async def _publish_state(self) -> None:
        await self.state.set_source_state(
            {
                "connected": self._connected,
                "streaming": self._streaming,
                "client_id": self.client_id,
            }
        )

    def _on_server_command(self, payload: object) -> None:
        source = getattr(payload, "source", None)
        if source is None:
            return
        loop = asyncio.get_running_loop()
        if source.command == "start":
            loop.create_task(self._start_streaming())
        elif source.command == "stop":
            loop.create_task(self._stop_streaming())

    async def _start_streaming(self) -> None:
        async with self._stream_lock:
            client = self._client
            if client is None or self._stream_task is not None:
                return
            from aiosendspin.models.player import SupportedAudioFormat
            from aiosendspin.models.types import AudioCodec

            capture_format = SupportedAudioFormat(
                codec=AudioCodec.PCM,
                channels=self.audio.channels,
                sample_rate=self.audio.sample_rate,
                bit_depth=16,
            )
            try:
                await self._await_time_sync(client)
                capture = client.create_source_capture(capture_format)
                await capture.start()
            except Exception as exc:
                await self.events.emit(
                    "sendspin_source_stream_failed", {"error": str(exc)}
                )
                return
            self._streaming = True
            self._stream_task = asyncio.create_task(
                self._pump(capture), name="sendspin-source-stream"
            )
            await self.events.emit("sendspin_source_stream_started", {})
            await self._publish_state()

    @staticmethod
    async def _await_time_sync(client: object) -> None:
        checker = getattr(client, "is_time_synchronized", None)
        if checker is None:
            return
        async with asyncio.timeout(TIME_SYNC_TIMEOUT_SECONDS):
            while not checker():
                await asyncio.sleep(0.05)

    async def _pump(self, capture: object) -> None:
        stream = self._pcm_stream_factory()
        try:
            async for chunk in stream:
                await capture.feed(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.events.emit(
                "sendspin_source_stream_failed", {"error": str(exc)}
            )
        finally:
            with suppress(Exception):
                await stream.aclose()
            with suppress(Exception):
                await capture.stop()
            if self._stream_task is asyncio.current_task():
                self._stream_task = None
                was_streaming = self._streaming
                self._streaming = False
                if was_streaming:
                    await self.events.emit("sendspin_source_stream_stopped", {})
                    await self._publish_state()

    async def _stop_streaming(self) -> None:
        async with self._stream_lock:
            task = self._stream_task
            self._stream_task = None
            was_streaming = self._streaming
            self._streaming = False
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if was_streaming:
            await self.events.emit("sendspin_source_stream_stopped", {})
            await self._publish_state()

    async def _send_signal(self, present: bool) -> None:
        client = self._client
        if client is None or not self._connected:
            return
        from aiosendspin.models.types import SignalState

        signal = SignalState.PRESENT if present else SignalState.ABSENT
        sender = getattr(client, "send_source_signal", None)
        if sender is None:
            # aiosendspin exposes signal reporting on the admitted connection
            # only; fall back to it until a client-level API exists.
            connection = getattr(client, "_admitted_connection", None)
            sender = getattr(connection, "send_source_signal", None)
        if sender is None:
            return
        with suppress(Exception):
            await sender(signal)

    async def _watch_signal(self, stop: asyncio.Event) -> None:
        last: bool | None = None
        while not stop.is_set():
            status = self.state.status
            active = bool(status.phono_active) if status is not None else False
            if self._connected and active != last:
                await self._send_signal(active)
                last = active
            elif not self._connected:
                # Resend after the next reconnect.
                last = None
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.signal_poll_seconds)

    async def run(self, stop: asyncio.Event) -> None:
        signal_task = asyncio.create_task(
            self._watch_signal(stop), name="sendspin-source-signal"
        )
        try:
            while not stop.is_set():
                try:
                    client = await self._client_factory()
                except Exception as exc:
                    await self.events.emit(
                        "sendspin_source_setup_failed", {"error": str(exc)}
                    )
                    await self._wait_retry(stop)
                    continue
                self._client = client
                disconnected = asyncio.Event()
                remove_command = client.add_server_command_listener(
                    self._on_server_command
                )
                remove_disconnect = client.add_disconnect_listener(disconnected.set)
                try:
                    await client.connect(self.config.server_url)
                except Exception as exc:
                    await self.events.emit(
                        "sendspin_source_connect_failed",
                        {"server": self.config.server_url, "error": str(exc)},
                    )
                    await self._teardown(client, remove_command, remove_disconnect)
                    await self._wait_retry(stop)
                    continue
                self._connected = True
                await self.events.emit(
                    "sendspin_source_connected", {"server": self.config.server_url}
                )
                await self._publish_state()

                stop_task = asyncio.create_task(stop.wait())
                drop_task = asyncio.create_task(disconnected.wait())
                try:
                    await asyncio.wait(
                        (stop_task, drop_task), return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    stop_task.cancel()
                    drop_task.cancel()

                self._connected = False
                await self._stop_streaming()
                await self._teardown(client, remove_command, remove_disconnect)
                await self.events.emit("sendspin_source_disconnected", {})
                await self._publish_state()
                if not stop.is_set():
                    await self._wait_retry(stop)
        finally:
            signal_task.cancel()
            with suppress(asyncio.CancelledError):
                await signal_task

    async def _teardown(
        self,
        client: object,
        remove_command: Callable[[], None],
        remove_disconnect: Callable[[], None],
    ) -> None:
        with suppress(Exception):
            remove_command()
        with suppress(Exception):
            remove_disconnect()
        with suppress(Exception):
            await client.disconnect()
        self._client = None

    async def _wait_retry(self, stop: asyncio.Event) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=self.reconnect_seconds)
