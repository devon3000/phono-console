"""Read-only observations of the Sendspin -> ALSA loopback -> HDMI path."""

from __future__ import annotations

import asyncio
from pathlib import Path
import time

from .interfaces import EventSink
from .state import StateStore

STATUS_FIELDS = {
    "state", "owner_pid", "trigger_time", "tstamp", "delay", "avail",
    "avail_max", "hw_ptr", "appl_ptr",
}


def read_fields(path: Path) -> dict[str, str]:
    try:
        with path.open() as handle:
            text = handle.read(4096)
    except OSError:
        return {}
    return {
        key.strip(): value.strip()
        for line in text.splitlines() if ":" in line
        for key, value in [line.split(":", 1)]
    }


def alsa_snapshot(loopback_pid: int | None, proc: Path = Path("/proc")) -> list[dict]:
    """Inspect existing PCM handles without opening or modifying an audio device."""
    result = []
    for status in sorted((proc / "asound").glob("card*/pcm*/sub*/status")):
        fields = read_fields(status)
        try:
            owner = int(fields.get("owner_pid", ""))
        except ValueError:
            continue
        role = "ma_loopback" if owner == loopback_pid else None
        if role is None:
            try:
                with (proc / str(owner) / "cmdline").open("rb") as handle:
                    command = handle.read(4096)
            except OSError:
                continue
            if b"/sendspin" not in command:
                continue
            role = "sendspin_player"
        params = read_fields(status.with_name("hw_params"))
        sample = {
            "pcm": str(status.parent.relative_to(proc / "asound")),
            "role": role,
            **{key: value for key, value in fields.items() if key in STATUS_FIELDS},
        }
        for key in ("rate", "buffer_size", "period_size", "channels", "format"):
            if key in params:
                sample[key] = params[key]
        try:
            rate = int(params["rate"].split()[0])
            if rate > 0:
                sample["delay_ms"] = round(int(fields["delay"]) * 1000 / rate, 2)
        except (KeyError, ValueError):
            pass
        result.append(sample)
        if len(result) == 16:
            break
    return result


class PlaybackDiagnostics:
    def __init__(self, state: StateStore, loopback, events: EventSink) -> None:
        self.state = state
        self.loopback = loopback
        self.events = events
        self.previous: dict[str, dict] = {}
        self.last_log = 0.0

    async def observe(self, pcms: list[dict], *, elapsed: float, wake_late_ms: float) -> None:
        current = {sample["pcm"]: sample for sample in pcms}
        changes = []
        for key, sample in current.items():
            old = self.previous.get(key)
            if old is None or any(
                old.get(field) != sample.get(field)
                for field in ("state", "owner_pid", "trigger_time")
            ):
                changes.append({"pcm": key, "reason": "handle_state_or_anchor_changed"})
            elif sample.get("delay_ms") is not None and old.get("delay_ms") is not None:
                delta = sample["delay_ms"] - old["delay_ms"]
                if abs(delta) >= 100:
                    changes.append({"pcm": key, "reason": "buffer_delay_jump", "delta_ms": delta})
            for field in ("hw_ptr", "appl_ptr"):
                try:
                    if old and int(sample[field]) < int(old[field]):
                        # Pointer wrap is possible; report an observation, not
                        # a claimed reset or an instruction to restart audio.
                        changes.append({"pcm": key, "reason": field + "_decreased"})
                except (KeyError, ValueError):
                    pass
        changes.extend(
            {"pcm": key, "reason": "handle_disappeared"}
            for key in self.previous.keys() - current.keys()
        )
        self.previous = current
        status = self.state.status
        payload = {
            "route": status.route.value if status else "startup",
            "requested_output": self.state.output_target.value,
            "routing_generation": self.state.routing_generation,
            "monotonic_seconds": round(elapsed, 3),
            "loopback_pid": self.loopback.pid,
            "loopback_running": self.loopback.running,
            "wake_late_ms": round(wake_late_ms, 2),
            "pcms": pcms,
        }
        await self.state.set_component(
            "ma_return_diagnostics", "ok" if pcms else "degraded",
            "read-only PCM observations" if pcms else "PCM status unavailable or inactive",
            **payload,
        )
        if changes or wake_late_ms >= 100 or elapsed - self.last_log >= 10:
            await self.events.emit("ma_return_timing", {**payload, "changes": changes})
            self.last_log = elapsed

    async def run(self, stop: asyncio.Event) -> None:
        previous = time.monotonic()
        while not stop.is_set():
            now = time.monotonic()
            try:
                pcms = await asyncio.to_thread(alsa_snapshot, self.loopback.pid)
                await self.observe(
                    pcms, elapsed=now,
                    wake_late_ms=max(0, (now - previous - 1) * 1000),
                )
            except Exception as exc:
                # Diagnostics must not take down the audio router.
                await self.state.set_component("ma_return_diagnostics", "degraded", str(exc))
            previous = now
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
