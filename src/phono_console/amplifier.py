from __future__ import annotations

import asyncio
import re
from contextlib import suppress

from .config import AmplifierConfig
from .interfaces import EventSink
from .state import StateStore


_AUDIO_STATUS = re.compile(r">> 51:7a:([0-9a-fA-F]{2})")
_POWER_STATUS = re.compile(r">> 51:90:([0-9a-fA-F]{2})")


def physical_address_bytes(address: str) -> tuple[int, int]:
    """Encode a dotted CEC physical address such as 1.0.0.0."""
    parts = [int(part, 16) for part in address.split(".")]
    return (parts[0] << 4) | parts[1], (parts[2] << 4) | parts[3]


def logical_to_cec(config: AmplifierConfig, volume: int) -> int:
    """Map a Music Assistant 1-100 volume onto the safe amplifier range."""
    logical = max(1, min(100, int(volume)))
    if config.volume_mid is not None:
        if logical <= config.volume_mid_position:
            return round(config.volume_min + (logical - 1)
                         * (config.volume_mid - config.volume_min)
                         / (config.volume_mid_position - 1))
        return round(config.volume_mid + (logical - config.volume_mid_position)
                     * (config.volume_max - config.volume_mid)
                     / (100 - config.volume_mid_position))
    span = config.volume_max - config.volume_min
    return round(config.volume_min + ((logical - 1) * span / 99))


def cec_to_logical(config: AmplifierConfig, volume: int) -> int:
    """Map a reported amplifier volume back onto Music Assistant's scale."""
    cec = max(config.volume_min, min(config.volume_max, int(volume)))
    if config.volume_mid is not None:
        if cec <= config.volume_mid:
            return round(1 + (cec - config.volume_min)
                         * (config.volume_mid_position - 1)
                         / (config.volume_mid - config.volume_min))
        return round(config.volume_mid_position + (cec - config.volume_mid)
                     * (100 - config.volume_mid_position)
                     / (config.volume_max - config.volume_mid))
    span = config.volume_max - config.volume_min
    return round(1 + ((cec - config.volume_min) * 99 / span))


class CecAmplifier:
    """Control and query a CEC audio system through ``cec-client``."""

    def __init__(
        self, config: AmplifierConfig, events: EventSink, state: StateStore
    ) -> None:
        self.config = config
        self.events = events
        self.state = state
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._reports: dict[str, asyncio.Queue] = {
            "71": asyncio.Queue(maxsize=1),
            "8f": asyncio.Queue(maxsize=1),
        }

    async def _open(self) -> asyncio.subprocess.Process:
        if self._process is not None and self._process.returncode is None:
            return self._process
        await self._stop_reader()
        self._process = await asyncio.create_subprocess_exec(
            "cec-client",
            "-d",
            "8",
            self.config.cec_device,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        for queue in self._reports.values():
            while not queue.empty():
                queue.get_nowait()
        self._reader_task = asyncio.create_task(self._read_reports(self._process))
        return self._process

    async def _read_reports(self, process: asyncio.subprocess.Process) -> None:
        """Continuously drain CEC stdout, including unsolicited remote reports."""
        assert process.stdout is not None
        try:
            while line := await process.stdout.readline():
                text = line.decode(errors="replace")
                for opcode, pattern in (("71", _AUDIO_STATUS), ("8f", _POWER_STATUS)):
                    match = pattern.search(text)
                    if not match:
                        continue
                    value = int(match.group(1), 16)
                    if opcode == "71" and (value & 0x7F) != 0x7F:
                        await self._publish_audio(value)
                    queue = self._reports[opcode]
                    if queue.full():
                        queue.get_nowait()
                    queue.put_nowait(value)
        finally:
            for queue in self._reports.values():
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(RuntimeError("CEC client ended before the amplifier replied"))

    async def _publish_audio(self, status: int) -> tuple[int, bool]:
        volume, muted = status & 0x7F, bool(status & 0x80)
        if volume == 0x7F:
            raise RuntimeError("CEC reported unknown volume")
        logical = 0 if muted else cec_to_logical(self.config, volume)
        # Retain power telemetry; an audio status report alone is not power-on.
        previous = self.state.components.get("amplifier", {})
        power = {key: previous[key] for key in ("powered", "awakened") if key in previous}
        await self._publish(volume=logical, cec_volume=volume, muted=muted, **power)
        return logical, muted

    async def start(self) -> None:
        """Prewarm the persistent CEC session before audio is detected."""
        if not self.config.enabled:
            return
        async with self._lock:
            await self._open()

    async def close(self) -> None:
        """Close the persistent CEC session."""
        async with self._lock:
            process, self._process = self._process, None
            if process is not None:
                await self._close(process)
            await self._stop_reader()

    async def _stop_reader(self) -> None:
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _invalidate(self, process: asyncio.subprocess.Process) -> None:
        if self._process is process:
            self._process = None
        await self._close(process)
        await self._stop_reader()

    async def _send(self, process: asyncio.subprocess.Process, command: str) -> None:
        opcode = command.rsplit(":", 1)[-1]
        if opcode in self._reports:
            queue = self._reports[opcode]
            while not queue.empty():
                queue.get_nowait()
        assert process.stdin is not None
        process.stdin.write(f"{command}\n".encode())
        await process.stdin.drain()

    async def _read_match(
        self,
        process: asyncio.subprocess.Process,
        pattern: re.Pattern[str],
        timeout: float = 3.0,
    ) -> int:
        async with asyncio.timeout(timeout):
            result = await self._reports["71" if pattern is _AUDIO_STATUS else "8f"].get()
            if isinstance(result, Exception):
                raise result
            if pattern is _AUDIO_STATUS and (result & 0x7F) == 0x7F:
                raise RuntimeError("CEC reported unknown volume")
            return result

    async def run(self, stop: asyncio.Event) -> None:
        """Read-only refresh; never wake, unmute, or restore a volume."""
        if not self.config.enabled:
            return
        while not stop.is_set():
            # Do not queue a background refresh behind a wake/volume command.
            if not self._lock.locked():
                async with self._lock:
                    try:
                        process = await self._open()
                        await self._send(process, f"tx 1{self.config.logical_address:x}:8f")
                        powered = await self._read_match(process, _POWER_STATUS)
                        previous = self.state.components.get("amplifier", {})
                        values = {key: previous[key] for key in ("volume", "cec_volume", "muted") if key in previous}
                        await self._publish(powered=powered == 0, **values)
                        if powered == 0:
                            await self._send(process, f"tx 1{self.config.logical_address:x}:71")
                            await self._read_match(process, _AUDIO_STATUS)
                    except Exception as exc:
                        await self.state.set_component("amplifier", "degraded", str(exc), enabled=True)
                        if self._process is not None:
                            await self._invalidate(self._process)
            try:
                await asyncio.wait_for(stop.wait(), timeout=3)
            except asyncio.TimeoutError:
                pass

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
            try:
                await asyncio.wait_for(process.wait(), timeout=1)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

    async def _publish(self, **details: object) -> None:
        await self.state.set_component(
            "amplifier",
            "ok",
            "CEC connected",
            enabled=self.config.enabled,
            wake_on_audio=self.config.wake_on_audio,
            physical_address=self.config.physical_address,
            **details,
        )

    async def power_on(self) -> None:
        if not self.config.enabled or not self.config.wake_on_audio:
            return
        async with self._lock:
            process = await self._open()
            try:
                high, low = physical_address_bytes(
                    self.config.physical_address
                )
                # Do not query power first. Starting cec-client and waiting for
                # that preflight reply delayed the visible wake by several
                # seconds. The TV-style handshake is safe and useful whether
                # the receiver is in standby or already on.
                await self._send(
                    process, f"tx 1f:82:{high:02x}:{low:02x}"
                )
                await asyncio.sleep(0.1)
                await self._send(
                    process,
                    f"tx 1{self.config.logical_address:x}:70:"
                    f"{high:02x}:{low:02x}",
                )
                awakened = True
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
                # The SR-300 reports CEC power=on before its HDMI audio and
                # front-panel volume controller are actually ready. Sending
                # volume keys or opening the ALSA route during that interval
                # can leave it in a half-awake state until it is power-cycled.
                # Hold volume restoration until the Yamaha's control plane is
                # ready. HDMI PCM is already flowing during this delay.
                if awakened and status == 0:
                    await asyncio.sleep(self.config.wake_settle_seconds)
                await self._publish(powered=status == 0, awakened=awakened)
                await self.events.emit(
                    "amplifier_power_on",
                    {"power_status": status, "awakened": awakened},
                )
            except Exception:
                await self._invalidate(process)
                raise

    async def set_volume(self, target: int) -> tuple[int, bool]:
        if not self.config.enabled:
            raise RuntimeError("CEC amplifier control is disabled")
        requested = max(0, min(100, int(target)))
        async with self._lock:
            process = await self._open()
            try:
                destination = self.config.logical_address
                await self._send(process, f"tx 1{destination:x}:71")
                status = await self._read_match(process, _AUDIO_STATUS)
                muted = bool(status & 0x80)
                current = status & 0x7F

                if requested == 0:
                    if not muted:
                        await self._send(process, f"tx 1{destination:x}:44:43")
                        await self._send(process, f"tx 1{destination:x}:45")
                else:
                    target = logical_to_cec(self.config, requested)
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
                logical_volume = 0 if muted else cec_to_logical(self.config, volume)
                await self._publish(
                    volume=logical_volume,
                    cec_volume=volume,
                    muted=muted,
                    powered=True,
                )
                await self.events.emit(
                    "amplifier_volume_changed",
                    {
                        "volume": logical_volume,
                        "cec_volume": volume,
                        "muted": muted,
                    },
                )
                return logical_volume, muted
            except Exception:
                await self._invalidate(process)
                raise
