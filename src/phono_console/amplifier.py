from __future__ import annotations

import asyncio
import re
from contextlib import suppress

from .config import AmplifierConfig
from .interfaces import EventSink
from .state import StateStore


_AUDIO_STATUS = re.compile(r">> 51:7a:([0-9a-fA-F]{2})")
_POWER_STATUS = re.compile(r">> 51:90:([0-9a-fA-F]{2})")


class CecAmplifier:
    """Control and query a CEC audio system through ``cec-client``."""

    def __init__(
        self, config: AmplifierConfig, events: EventSink, state: StateStore
    ) -> None:
        self.config = config
        self.events = events
        self.state = state
        self._lock = asyncio.Lock()

    async def _open(self) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            "cec-client",
            "-d",
            "8",
            self.config.cec_device,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

    @staticmethod
    async def _send(process: asyncio.subprocess.Process, command: str) -> None:
        assert process.stdin is not None
        process.stdin.write(f"{command}\n".encode())
        await process.stdin.drain()

    @staticmethod
    async def _read_match(
        process: asyncio.subprocess.Process,
        pattern: re.Pattern[str],
        timeout: float = 3.0,
    ) -> int:
        assert process.stdout is not None

        async def read() -> int:
            while line := await process.stdout.readline():
                match = pattern.search(line.decode(errors="replace"))
                if match:
                    return int(match.group(1), 16)
            raise RuntimeError("CEC client ended before the amplifier replied")

        async with asyncio.timeout(timeout):
            return await read()

    @staticmethod
    async def _close(process: asyncio.subprocess.Process) -> None:
        if process.stdin is not None:
            with suppress(Exception):
                process.stdin.write(b"q\n")
                await process.stdin.drain()
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=1)
        if process.returncode is None:
            process.terminate()
            with suppress(Exception):
                await process.wait()

    async def _publish(self, **details: object) -> None:
        await self.state.set_component(
            "amplifier", "ok", "CEC connected", **details
        )

    async def power_on(self) -> None:
        if not self.config.enabled or not self.config.wake_on_audio:
            return
        async with self._lock:
            process = await self._open()
            try:
                await self._send(process, f"on {self.config.logical_address}")
                status = 1
                deadline = asyncio.get_running_loop().time() + 8
                while status != 0 and asyncio.get_running_loop().time() < deadline:
                    await self._send(
                        process, f"tx 1{self.config.logical_address:x}:8f"
                    )
                    status = await self._read_match(
                        process, _POWER_STATUS, timeout=2
                    )
                    if status != 0:
                        await asyncio.sleep(0.25)
                await self._publish(powered=status == 0)
                await self.events.emit(
                    "amplifier_power_on", {"power_status": status}
                )
            finally:
                await self._close(process)

    async def set_volume(self, target: int) -> tuple[int, bool]:
        if not self.config.enabled:
            raise RuntimeError("CEC amplifier control is disabled")
        target = max(0, min(100, int(target)))
        async with self._lock:
            process = await self._open()
            try:
                destination = self.config.logical_address
                await self._send(process, f"tx 1{destination:x}:71")
                status = await self._read_match(process, _AUDIO_STATUS)
                muted = bool(status & 0x80)
                current = status & 0x7F

                if target == 0:
                    if not muted:
                        await self._send(process, f"tx 1{destination:x}:44:43")
                        await self._send(process, f"tx 1{destination:x}:45")
                else:
                    if muted:
                        await self._send(process, f"tx 1{destination:x}:44:43")
                        await self._send(process, f"tx 1{destination:x}:45")
                    key = 0x41 if target > current else 0x42
                    for _ in range(abs(target - current)):
                        await self._send(
                            process, f"tx 1{destination:x}:44:{key:02x}"
                        )
                        await self._send(process, f"tx 1{destination:x}:45")
                        await asyncio.sleep(0.03)

                await self._send(process, f"tx 1{destination:x}:71")
                result = await self._read_match(process, _AUDIO_STATUS)
                volume = result & 0x7F
                muted = bool(result & 0x80)
                await self._publish(volume=volume, muted=muted, powered=True)
                await self.events.emit(
                    "amplifier_volume_changed",
                    {"volume": volume, "muted": muted},
                )
                return volume, muted
            finally:
                await self._close(process)
