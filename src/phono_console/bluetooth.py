from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress

from .config import BluetoothConfig
from .interfaces import EventSink
from .state import StateStore


_LOGGER = logging.getLogger(__name__)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


async def _run_bluetoothctl(*args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        "bluetoothctl",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return (
        process.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


def parse_player_show(output: str) -> dict[str, object]:
    """Parse ``bluetoothctl player.show`` into dashboard-safe media state."""
    values: dict[str, str] = {}
    player_path: str | None = None
    for raw_line in _ANSI_ESCAPE.sub("", output).splitlines():
        line = raw_line.strip()
        if line.startswith("Player "):
            player_path = line.split()[1]
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    if player_path is None:
        return {"media_available": False}

    def integer(key: str) -> int | None:
        value = values.get(key)
        if value is None:
            return None
        try:
            return int(value, 0)
        except ValueError:
            return None

    track = {
        "title": values.get("Track.Title"),
        "artist": values.get("Track.Artist"),
        "album": values.get("Track.Album"),
        "duration_ms": integer("Track.Duration"),
        "track_number": integer("Track.TrackNumber"),
        "number_of_tracks": integer("Track.NumberOfTracks"),
    }
    return {
        "media_available": True,
        "media_player_path": player_path,
        "media_player_name": values.get("Name"),
        "media_status": values.get("Status", "unknown").lower(),
        "media_position_ms": integer("Position"),
        "media_track": {key: value for key, value in track.items() if value is not None},
    }


def parse_transport_paths(output: str) -> list[str]:
    """Extract BlueZ MediaTransport object paths from bluetoothctl output."""
    paths: list[str] = []
    for raw_line in _ANSI_ESCAPE.sub("", output).splitlines():
        parts = raw_line.strip().split()
        if len(parts) >= 2 and parts[0] == "Transport" and parts[1].startswith("/"):
            paths.append(parts[1])
    return paths


def parse_transport_volume(output: str) -> int | None:
    """Return an active A2DP sink transport's AVRCP volume (0-127)."""
    values: dict[str, str] = {}
    for raw_line in _ANSI_ESCAPE.sub("", output).splitlines():
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    if values.get("State", "").lower() != "active":
        return None
    if "Audio Sink" not in values.get("UUID", ""):
        return None
    match = re.search(r"(?:0x[0-9a-fA-F]+|\d+)", values.get("Volume", ""))
    if match is None:
        return None
    return max(0, min(127, int(match.group(0), 0)))


def map_transport_volume(raw_volume: int, minimum: int, maximum: int) -> int:
    """Compress AVRCP's 1..127 range while preserving zero as mute."""
    raw = max(0, min(127, int(raw_volume)))
    if raw == 0:
        return 0
    return round(minimum + (raw - 1) * (maximum - minimum) / 126)


class BluetoothManager:
    """Supervise one headless BlueZ agent and a time-limited pairing window."""

    def __init__(
        self,
        config: BluetoothConfig,
        events: EventSink,
        state: StateStore,
        *,
        command_runner: Callable[..., Awaitable[tuple[int, str, str]]] = _run_bluetoothctl,
        media_poll_seconds: float = 1.0,
        volume_action: Callable[[int], Awaitable[None]] | None = None,
    ):
        self.config = config
        self.events = events
        self.state = state
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pairing_task: asyncio.Task[None] | None = None
        self._pairing_active = False
        self._agent_ready = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self._command_runner = command_runner
        self._media_poll_seconds = media_poll_seconds
        self._volume_action = volume_action
        self._last_transport_volume: int | None = None

    def set_volume_action(
        self, action: Callable[[int], Awaitable[None]] | None
    ) -> None:
        self._volume_action = action

    @property
    def playback_status(self) -> str | None:
        value = self.state.bluetooth.get("media_status")
        return str(value) if value is not None else None

    async def media_command(self, command: str) -> None:
        """Send an AVRCP transport command to the connected phone."""
        if command not in {"play", "pause", "next", "previous"}:
            raise ValueError("unsupported Bluetooth media command")
        code, stdout, stderr = await self._command_runner(f"player.{command}")
        if code != 0:
            raise RuntimeError(
                stderr.strip() or stdout.strip() or f"Bluetooth {command} failed"
            )
        await self.events.emit("bluetooth_media_command", {"command": command})
        await self._refresh_media_state()

    async def _refresh_media_state(self) -> None:
        code, stdout, stderr = await self._command_runner("player.show")
        media = (
            parse_player_show(stdout)
            if code == 0
            else {"media_available": False, "media_error": stderr.strip()}
        )
        previous = {
            key: value
            for key, value in self.state.bluetooth.items()
            if key.startswith("media_")
        }
        if media != previous:
            updated = {
                key: value
                for key, value in self.state.bluetooth.items()
                if not key.startswith("media_")
            }
            updated.update(media)
            await self.state.set_bluetooth_state(updated)
            if media.get("media_available"):
                track = media.get("media_track")
                await self.events.emit(
                    "bluetooth_media_updated",
                    {
                        "status": media.get("media_status", "unknown"),
                        **({"track": track} if track else {}),
                    },
                )

    async def _refresh_transport_volume(self) -> None:
        code, stdout, _ = await self._command_runner("transport.list")
        if code != 0:
            return
        raw_volume: int | None = None
        for path in parse_transport_paths(stdout):
            show_code, show_stdout, _ = await self._command_runner(
                "transport.show", path
            )
            if show_code == 0:
                raw_volume = parse_transport_volume(show_stdout)
            if raw_volume is not None:
                break
        if raw_volume is None or raw_volume == self._last_transport_volume:
            return
        self._last_transport_volume = raw_volume
        volume = map_transport_volume(
            raw_volume, self.config.volume_min, self.config.volume_max
        )
        await self._publish(bluetooth_volume=volume, bluetooth_volume_raw=raw_volume)
        await self.events.emit(
            "bluetooth_volume_changed", {"volume": volume, "raw_volume": raw_volume}
        )
        if self._volume_action is not None:
            await self._volume_action(volume)

    async def _watch_media(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self._refresh_media_state()
                await self._refresh_transport_volume()
                await self.state.set_component(
                    "bluetooth_media",
                    "ok",
                    self.playback_status or "no connected media player",
                )
            except Exception as exc:
                await self.state.set_component("bluetooth_media", "degraded", str(exc))
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self._media_poll_seconds)

    async def _send_command(self, *command: str) -> None:
        """Send a command through the process that owns the BlueZ agent."""
        process = self._process
        if process is None or process.returncode is not None or process.stdin is None:
            raise RuntimeError("Bluetooth agent is not running")
        async with self._write_lock:
            process.stdin.write((" ".join(command) + "\n").encode())
            await process.stdin.drain()

    async def _read_agent_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        pending = ""
        while True:
            chunk = await process.stdout.read(512)
            if not chunk:
                return
            text = chunk.decode(errors="replace")
            pending = (pending + text)[-2048:]
            for line in text.splitlines():
                if line.strip():
                    _LOGGER.info("bluetoothctl: %s", line.strip())
            if "Agent registered" in pending:
                self._agent_ready.set()
            prompt = re.search(r"([^\r\n]*(?:yes/no|yes/no/always)[^\r\n]*)", pending, re.I)
            if prompt is not None:
                accepted = self._pairing_active
                await self._answer_agent_prompt(accepted, prompt.group(1).strip())
                pending = ""

    async def _answer_agent_prompt(self, accepted: bool, prompt: str) -> None:
        process = self._process
        if process is None or process.returncode is not None or process.stdin is None:
            return
        async with self._write_lock:
            process.stdin.write(b"yes\n" if accepted else b"no\n")
            await process.stdin.drain()
        event = (
            "bluetooth_pairing_prompt_accepted"
            if accepted
            else "bluetooth_pairing_prompt_rejected"
        )
        await self.events.emit(event, {"prompt": prompt})
        _LOGGER.info("%s: %s", event, prompt)

    async def _publish(self, **values: object) -> None:
        current = dict(self.state.bluetooth)
        current.update(values)
        await self.state.set_bluetooth_state(current)

    async def open_pairing(self) -> None:
        self._pairing_active = True
        await self._send_command("pairable", "on")
        await self._send_command("discoverable", "on")
        if self._pairing_task is not None:
            self._pairing_task.cancel()
        self._pairing_task = asyncio.create_task(self._close_after_timeout())
        await self._publish(
            pairing=True,
            pairing_seconds=self.config.pairing_window_seconds,
        )
        await self.events.emit(
            "bluetooth_pairing_opened",
            {"seconds": self.config.pairing_window_seconds},
        )

    async def close_pairing(self) -> None:
        self._pairing_active = False
        task = self._pairing_task
        self._pairing_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        with suppress(Exception):
            await self._send_command("discoverable", "off")
            await self._send_command("pairable", "off")
        trusted = await self._trust_paired_devices()
        await self._publish(
            pairing=False, pairing_seconds=0, trusted_devices=trusted
        )
        await self.events.emit("bluetooth_pairing_closed", {})

    async def _trust_paired_devices(self) -> list[dict[str, str]]:
        """Trust paired devices so reconnects survive a headless reboot."""
        process = await asyncio.create_subprocess_exec(
            "bluetoothctl",
            "devices",
            "Paired",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        devices: list[dict[str, str]] = []
        if process.returncode != 0:
            return devices
        for raw_line in stdout.decode(errors="replace").splitlines():
            parts = raw_line.strip().split(maxsplit=2)
            if len(parts) < 2 or parts[0] != "Device":
                continue
            address = parts[1]
            name = parts[2] if len(parts) == 3 else address
            trust = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                "trust",
                address,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if await trust.wait() == 0:
                devices.append({"address": address, "name": name})
        return devices

    async def device_action(self, action: str, address: str) -> None:
        if action not in {"disconnect", "remove"}:
            raise ValueError("unsupported Bluetooth device action")
        if re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", address) is None:
            raise ValueError("invalid Bluetooth address")
        process = await asyncio.create_subprocess_exec(
            "bluetoothctl",
            action,
            address,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                stderr.decode(errors="replace").strip()
                or f"Bluetooth {action} failed"
            )
        await self.events.emit(
            f"bluetooth_device_{'forgotten' if action == 'remove' else 'disconnected'}",
            {"address": address},
        )

    async def _close_after_timeout(self) -> None:
        try:
            await asyncio.sleep(self.config.pairing_window_seconds)
            await self.close_pairing()
        except asyncio.CancelledError:
            raise

    async def run(self, stop: asyncio.Event) -> None:
        if not self.config.enabled:
            await self._publish(enabled=False, agent=False, pairing=False)
            return
        media_task = asyncio.create_task(
            self._watch_media(stop), name="bluetooth-media-monitor"
        )
        while not stop.is_set():
            try:
                self._agent_ready.clear()
                self._process = await asyncio.create_subprocess_exec(
                    "bluetoothctl",
                    "--agent",
                    "NoInputNoOutput",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                self._reader_task = asyncio.create_task(self._read_agent_output())
                async with asyncio.timeout(5):
                    await self._agent_ready.wait()
                await self._send_command("default-agent")
                await self._send_command("power", "on")
                await self._send_command("system-alias", self.config.alias)
                await self._send_command("discoverable", "off")
                await self._send_command("pairable", "off")
                await self._publish(enabled=True, agent=True, pairing=False)
                await self.state.set_component("bluetooth_agent", "ok", "ready")
                await self.events.emit("bluetooth_agent_started", {})
                wait_stop = asyncio.create_task(stop.wait())
                wait_process = asyncio.create_task(self._process.wait())
                done, pending = await asyncio.wait(
                    (wait_stop, wait_process), return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                if wait_stop in done:
                    break
                await self.state.set_component(
                    "bluetooth_agent", "degraded", "bluetoothctl exited"
                )
            except Exception as exc:
                await self._stop_agent()
                await self._publish(enabled=True, agent=False, error=str(exc))
                await self.state.set_component("bluetooth_agent", "degraded", str(exc))
                await self.events.emit("bluetooth_agent_failed", {"error": str(exc)})
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=5)
        media_task.cancel()
        with suppress(asyncio.CancelledError):
            await media_task
        await self.close()

    async def _stop_agent(self) -> None:
        process = self._process
        self._process = None
        self._pairing_active = False
        if process is not None and process.returncode is None:
            process.terminate()
            with suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=2)
            if process.returncode is None:
                process.kill()
                await process.wait()
        reader = self._reader_task
        self._reader_task = None
        if reader is not None:
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader

    async def close(self) -> None:
        if self._pairing_task is not None:
            self._pairing_task.cancel()
            self._pairing_task = None
        await self._stop_agent()
        await self._publish(agent=False, pairing=False)
