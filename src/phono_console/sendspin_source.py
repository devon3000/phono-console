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
import math
import sys
from array import array
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Protocol

from .alsa import CaptureUnavailable
from .audio_engine_protocol import FrameFlags, TimestampedPcm
from .config import AudioConfig, SendspinConfig
from .interfaces import EventSink
from .policy import Source
from .state import StateStore

LOGGER = logging.getLogger(__name__)

IDENTITY_FILE = "identity.key"
PAIRING_FILE = "pairing.json"
TIME_SYNC_TIMEOUT_SECONDS = 10.0
SIGNAL_RELEASE_SECONDS = 1.0

PcmStreamFactory = Callable[[], AsyncIterator[bytes | TimestampedPcm]]
ClientFactory = Callable[[], Awaitable[object]]
SourceStopAction = Callable[[Source], Awaitable[None]]


def apply_gain_s16le(pcm: bytes, gain_db: float) -> bytes:
    """Apply gain to signed 16-bit PCM with saturation instead of wraparound."""
    if not pcm or gain_db == 0:
        return pcm
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    factor = math.pow(10.0, gain_db / 20.0)
    for index, sample in enumerate(samples):
        samples[index] = max(-32768, min(32767, round(sample * factor)))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


class BluetoothMedia(Protocol):
    @property
    def playback_status(self) -> str | None: ...

    async def media_command(self, command: str) -> None: ...


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
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    chunk_bytes = chunk_frames * channels * 2
    try:
        while True:
            yield await process.stdout.readexactly(chunk_bytes)
    except asyncio.IncompleteReadError as exc:
        returncode = await process.wait()
        error = b""
        if process.stderr is not None:
            with suppress(TimeoutError):
                error = await asyncio.wait_for(process.stderr.read(4096), timeout=0.25)
        message = error.decode(errors="replace").strip()
        raise CaptureUnavailable(
            message or f"source capture exited with status {returncode}"
        ) from exc
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
        signal_release_seconds: float = SIGNAL_RELEASE_SECONDS,
        source_devices: dict[Source, str] | None = None,
        pcm_stream_factories: dict[Source, PcmStreamFactory] | None = None,
        bluetooth_media: BluetoothMedia | None = None,
        source_stop_action: SourceStopAction | None = None,
        phono_stop_grace_seconds: float = 1.0,
    ) -> None:
        self.config = sendspin
        self.audio = audio
        self.events = events
        self.state = state
        self.client_id: str | None = None
        self.pairing_token: str | None = None
        self._client_factory = client_factory or self._default_client_factory
        self._pcm_stream_factory = pcm_stream_factory or self._default_pcm_stream
        self.reconnect_seconds = reconnect_seconds
        self.signal_poll_seconds = signal_poll_seconds
        self.signal_release_seconds = signal_release_seconds
        self._client: object | None = None
        self._connected = False
        self._stream_task: asyncio.Task[None] | None = None
        self._streaming = False
        self._stream_lock = asyncio.Lock()
        # Server commands may arrive back-to-back while Music Assistant
        # rebuilds a group. Preserve their wire order so cleanup for an older
        # stop cannot race a newer start and invalidate its capture session.
        self._command_lock = asyncio.Lock()
        self._stream_requested = False
        self._stream_retry_task: asyncio.Task[None] | None = None
        self._stream_error: str | None = None
        self._selected_source = Source.PHONO
        self._distribution_enabled = True
        self._source_devices = source_devices or {
            Source.PHONO: audio.capture_device,
        }
        self._source_distribution_enabled = {
            source: True for source in self._source_devices
        }
        self._pcm_stream_factories = pcm_stream_factories or {}
        self._bluetooth_media = bluetooth_media
        self._source_stop_action = source_stop_action
        self._phono_stop_grace_seconds = phono_stop_grace_seconds
        self._phono_stop_task: asyncio.Task[None] | None = None
        self._bluetooth_pause_latched = False
        self._bluetooth_pause_observed = False

    @property
    def phono_stop_pending(self) -> bool:
        return self._phono_stop_task is not None

    def _cancel_phono_stop(self) -> None:
        if self._phono_stop_task is not None:
            self._phono_stop_task.cancel()
            self._phono_stop_task = None

    async def _commit_phono_stop(self) -> None:
        task = asyncio.current_task()
        try:
            await asyncio.sleep(self._phono_stop_grace_seconds)
            if self._source_stop_action is not None:
                await self._source_stop_action(Source.PHONO)
        except asyncio.CancelledError:
            return
        finally:
            if self._phono_stop_task is task:
                self._phono_stop_task = None
            await self._publish_state()

    async def set_distribution_enabled(self, enabled: bool) -> None:
        self._distribution_enabled = enabled
        if not enabled:
            await self._send_signal(False)
            await self._stop_streaming()
        await self._publish_state()

    async def set_source_distribution_enabled(
        self, source: Source, enabled: bool
    ) -> None:
        self._source_distribution_enabled[source] = enabled
        if source is self._selected_source and not enabled:
            await self._send_signal(False)
            await self._stop_streaming()
        await self._publish_state()

    def _default_pcm_stream(self) -> AsyncIterator[bytes | TimestampedPcm]:
        factory = self._pcm_stream_factories.get(self._selected_source)
        if factory is not None:
            return factory()
        # 20 ms chunks keep feed timestamps fine-grained without hammering
        # the websocket.
        chunk_frames = max(1, self.audio.sample_rate // 50)
        return arecord_pcm_stream(
            self._source_devices[self._selected_source],
            self.audio.sample_rate,
            self.audio.channels,
            chunk_frames,
        )

    async def select_source(self, source: Source) -> None:
        """Select the automatically winning local source for publication."""
        if source not in self._source_devices:
            raise ValueError(f"source is not publishable: {source.value}")
        if source is self._selected_source:
            return
        was_requested = self._stream_requested
        await self._stop_streaming()
        self._selected_source = source
        self._stream_requested = was_requested
        await self.events.emit("sendspin_source_selected", {"source": source.value})
        if was_requested:
            await self._start_streaming()
        await self._publish_state()

    async def _default_client_factory(self) -> object:
        from aiosendspin.client import PairingSupport, SendspinClient
        from aiosendspin.models.source import (
            ClientHelloSourceFeatures,
            ClientHelloSourceSupport,
        )
        from aiosendspin.models.types import Roles
        from aiosendspin.noise.trust_store import FileClientPairingStore
        from aiosendspin.noise import PSKPairingToken, encode_token
        from aiosendspin.noise.keys import generate_psk, psk_id_for
        from aiosendspin.noise.trust_store import PairingPsk

        state_dir = Path(self.config.state_dir)
        identity = await asyncio.to_thread(load_or_create_identity, state_dir)
        store = await FileClientPairingStore.open(state_dir / PAIRING_FILE)
        self.client_id = identity.peer_id
        pairing_psk = await store.pairing_psk()
        if pairing_psk is None:
            secret = generate_psk()
            pairing_psk = PairingPsk(psk_id=psk_id_for(secret), psk=secret)
            await store.set_pairing_psk(pairing_psk)
        self.pairing_token = encode_token(
            PSKPairingToken(
                client_id=self.client_id,
                pairing_psk=pairing_psk.psk,
            )
        )
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
                "pairing_token": self.pairing_token,
                "stream_requested": self._stream_requested,
                "phono_stop_pending": self.phono_stop_pending,
                "error": self._stream_error,
                "selected_source": self._selected_source.value,
            }
        )
        await self.state.set_component(
            "sendspin_source",
            "ok" if self._connected and self._stream_error is None else "degraded",
            self._stream_error
            or ("connected" if self._connected else "disconnected"),
            streaming=self._streaming,
        )

    def _on_server_command(self, payload: object) -> None:
        source = getattr(payload, "source", None)
        if source is None:
            return
        loop = asyncio.get_running_loop()
        if source.command == "start":
            loop.create_task(self._handle_server_command("start"))
        elif source.command == "stop":
            loop.create_task(self._handle_server_command("stop"))

    async def _handle_server_command(self, command: str) -> None:
        async with self._command_lock:
            await self.events.emit("sendspin_source_command", {"command": command})
            distribution_allowed = bool(
                self._distribution_enabled
                and self._source_distribution_enabled.get(
                    self._selected_source, False
                )
            )
            if command == "start" and not distribution_allowed:
                # Music Assistant can deliver a delayed source.start while a
                # previous distributed session is being torn down. Local
                # mode is authoritative: never reopen capture and broadcast
                # behind the dashboard's back.
                await self._send_signal(False)
                await self._stop_streaming()
                await self.events.emit(
                    "sendspin_source_start_ignored",
                    {
                        "source": self._selected_source.value,
                        "reason": "distribution_disabled",
                    },
                )
                return
            if self._selected_source is Source.BLUETOOTH and self._bluetooth_media:
                if command == "start":
                    self._bluetooth_pause_latched = False
                    self._bluetooth_pause_observed = False
                    transport_command = "play"
                else:
                    # Latch before sending Pause so still-buffered PCM cannot
                    # immediately trigger line-sense auto-play again.
                    self._bluetooth_pause_latched = True
                    self._bluetooth_pause_observed = False
                    transport_command = "pause"
                try:
                    await self._bluetooth_media.media_command(transport_command)
                except Exception as exc:
                    await self.events.emit(
                        "bluetooth_media_command_failed",
                        {"command": transport_command, "error": str(exc)},
                    )
            if command == "start":
                self._cancel_phono_stop()
                await self._start_streaming()
            else:
                await self._stop_streaming()
                if self._selected_source is Source.PHONO:
                    self._cancel_phono_stop()
                    self._phono_stop_task = asyncio.create_task(
                        self._commit_phono_stop()
                    )
                    await self._publish_state()

    async def _start_streaming(self) -> None:
        self._stream_requested = True
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
                self._stream_error = str(exc)
                await self.events.emit(
                    "sendspin_source_stream_failed", {"error": str(exc)}
                )
                await self._publish_state()
                self._schedule_stream_retry()
                return
            self._stream_error = None
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
        error: str | None = None
        capture_anchor_us: int | None = None
        captured_frames = 0
        frame_stride = self.audio.channels * 2
        gain_db = (
            self.config.bluetooth_gain_db
            if self._selected_source is Source.BLUETOOTH
            else self.config.phono_gain_db
        )
        try:
            async for chunk in stream:
                if isinstance(chunk, TimestampedPcm):
                    if chunk.flags & FrameFlags.DISCONTINUITY and captured_frames:
                        raise CaptureUnavailable(
                            "timestamped source capture crossed a discontinuity"
                        )
                    pcm = apply_gain_s16le(chunk.pcm, gain_db)
                    timestamp_us = chunk.first_sample_time_us
                    frames = chunk.frames
                    await capture.feed(pcm, capture_timestamp_us=timestamp_us)
                    captured_frames += frames
                    continue
                pcm = apply_gain_s16le(chunk, gain_db)
                frames = len(pcm) // frame_stride
                if capture_anchor_us is None:
                    client = self._client
                    clock = getattr(client, "now_us", None)
                    now_us = (
                        int(clock())
                        if clock is not None
                        else int(asyncio.get_running_loop().time() * 1_000_000)
                    )
                    # readexactly returns after the final sample in this block
                    # was captured; timestamp the first sample, not send time.
                    capture_anchor_us = (
                        now_us - frames * 1_000_000 // self.audio.sample_rate
                    )
                timestamp_us = (
                    capture_anchor_us
                    + captured_frames * 1_000_000 // self.audio.sample_rate
                )
                await capture.feed(pcm, capture_timestamp_us=timestamp_us)
                captured_frames += frames
            error = "PCM capture ended unexpectedly"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = str(exc)
        finally:
            with suppress(Exception):
                await stream.aclose()
            with suppress(Exception):
                await capture.stop()
            if self._stream_task is asyncio.current_task():
                self._stream_task = None
                was_streaming = self._streaming
                self._streaming = False
                if error is not None and self._stream_requested:
                    self._stream_error = error
                    await self.events.emit(
                        "sendspin_source_stream_failed", {"error": error}
                    )
                if was_streaming:
                    await self.events.emit("sendspin_source_stream_stopped", {})
                    await self._publish_state()
                if error is not None and self._stream_requested:
                    self._schedule_stream_retry()

    def _schedule_stream_retry(self) -> None:
        if (
            not self._stream_requested
            or not self._connected
            or (
                self._stream_retry_task is not None
                and not self._stream_retry_task.done()
            )
        ):
            return
        self._stream_retry_task = asyncio.create_task(
            self._retry_stream(), name="sendspin-source-capture-retry"
        )

    async def _retry_stream(self) -> None:
        try:
            while self._stream_requested and self._connected:
                await asyncio.sleep(self.reconnect_seconds)
                if not self._stream_requested or not self._connected:
                    break
                await self._start_streaming()
                if self._stream_task is not None:
                    break
        except asyncio.CancelledError:
            raise
        finally:
            if self._stream_retry_task is asyncio.current_task():
                self._stream_retry_task = None

    async def _stop_streaming(self) -> None:
        self._stream_requested = False
        retry_task = self._stream_retry_task
        self._stream_retry_task = None
        if retry_task is not None:
            retry_task.cancel()
            with suppress(asyncio.CancelledError):
                await retry_task
        async with self._stream_lock:
            task = self._stream_task
            self._stream_task = None
            was_streaming = self._streaming
            self._streaming = False
            self._stream_error = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if was_streaming:
            await self.events.emit("sendspin_source_stream_stopped", {})
            await self._publish_state()

    async def _send_signal(self, present: bool) -> bool:
        client = self._client
        if client is None or not self._connected:
            return False
        from aiosendspin.models.types import SignalState

        signal = SignalState.PRESENT if present else SignalState.ABSENT
        sender = getattr(client, "send_source_signal", None)
        if sender is None:
            # aiosendspin exposes signal reporting on the admitted connection
            # only; fall back to it until a client-level API exists.
            connection = getattr(client, "_admitted_connection", None)
            sender = getattr(connection, "send_source_signal", None)
        if sender is None:
            await self.events.emit(
                "sendspin_source_signal_failed",
                {"signal": signal.value, "error": "client has no signal sender"},
            )
            return False
        try:
            await sender(signal)
        except Exception as exc:
            await self.events.emit(
                "sendspin_source_signal_failed",
                {"signal": signal.value, "error": str(exc)},
            )
            return False
        await self.events.emit(
            "sendspin_source_signal_sent", {"signal": signal.value}
        )
        return True

    async def _watch_signal(self, stop: asyncio.Event) -> None:
        last: bool | None = None
        absent_since: float | None = None
        while not stop.is_set():
            status = self.state.status
            selected = None
            if status is not None:
                if status.phono_active:
                    selected = Source.PHONO
                elif status.bluetooth_active:
                    selected = Source.BLUETOOTH
            if selected in self._source_devices:
                await self.select_source(selected)
            detected = (
                bool(status.phono_active or status.bluetooth_active)
                if status is not None
                else False
            )
            source_enabled = self._source_distribution_enabled.get(
                selected or self._selected_source, True
            )
            if self._bluetooth_pause_latched and selected is Source.BLUETOOTH:
                media_status = (
                    self._bluetooth_media.playback_status
                    if self._bluetooth_media is not None
                    else None
                )
                if media_status in {"paused", "stopped"}:
                    self._bluetooth_pause_observed = True
                elif self._bluetooth_pause_observed and media_status == "playing":
                    # A new Play from the phone is intentional and may once
                    # again drive MA's line-sense auto-play policy.
                    self._bluetooth_pause_latched = False
                    self._bluetooth_pause_observed = False
                if self._bluetooth_pause_latched:
                    detected = False
            if not self._distribution_enabled or not source_enabled:
                active = False
                absent_since = None
            elif detected:
                active = True
                absent_since = None
            elif last is True:
                if absent_since is None:
                    absent_since = asyncio.get_running_loop().time()
                active = (
                    asyncio.get_running_loop().time() - absent_since
                    < self.signal_release_seconds
                )
            else:
                active = False
            if self._connected and active != last:
                if await self._send_signal(active):
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
            self._cancel_phono_stop()
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
