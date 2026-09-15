from __future__ import annotations

import asyncio
import re
from contextlib import suppress

from .config import BluetoothConfig
from .interfaces import EventSink
from .state import StateStore


class BluetoothManager:
    """Supervise a headless BlueZ agent and time-limited pairing window."""

    def __init__(self, config: BluetoothConfig, events: EventSink, state: StateStore):
        self.config = config
        self.events = events
        self.state = state
        self._process: asyncio.subprocess.Process | None = None
        self._pairing_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()

    async def _command(self, command: str) -> None:
        """Run a BlueZ command and wait for its result.

        Writing several commands into an interactive bluetoothctl process can
        race adapter discovery at boot. One-shot invocations provide an exit
        status and make configuration deterministic.
        """
        async with self._write_lock:
            process = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                "--timeout",
                "10",
                *command.split(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = (stderr or stdout).decode(errors="replace").strip()
            raise RuntimeError(detail or f"Bluetooth command failed: {command}")

    async def _publish(self, **values: object) -> None:
        current = dict(self.state.bluetooth)
        current.update(values)
        await self.state.set_bluetooth_state(current)

    async def open_pairing(self) -> None:
        await self._command("pairable on")
        await self._command("discoverable on")
        if self._pairing_task is not None:
            self._pairing_task.cancel()
        self._pairing_task = asyncio.create_task(self._close_after_timeout())
        await self._publish(pairing=True, pairing_seconds=self.config.pairing_window_seconds)
        await self.events.emit(
            "bluetooth_pairing_opened",
            {"seconds": self.config.pairing_window_seconds},
        )

    async def close_pairing(self) -> None:
        task = self._pairing_task
        self._pairing_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        with suppress(Exception):
            await self._command("discoverable off")
            await self._command("pairable off")
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
        while not stop.is_set():
            try:
                self._process = await asyncio.create_subprocess_exec(
                    "bluetoothctl",
                    "--agent",
                    "NoInputNoOutput",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await self._command("power on")
                await self._command(f"system-alias {self.config.alias}")
                await self._command("discoverable off")
                await self._command("pairable off")
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
                await self._publish(enabled=True, agent=False, error=str(exc))
                await self.state.set_component("bluetooth_agent", "degraded", str(exc))
                await self.events.emit("bluetooth_agent_failed", {"error": str(exc)})
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=5)
        await self.close()

    async def close(self) -> None:
        if self._pairing_task is not None:
            self._pairing_task.cancel()
            self._pairing_task = None
        process = self._process
        self._process = None
        if process is not None and process.returncode is None:
            process.terminate()
            with suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=2)
            if process.returncode is None:
                process.kill()
                await process.wait()
        await self._publish(agent=False, pairing=False)
