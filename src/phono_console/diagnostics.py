from __future__ import annotations

import asyncio
import json
import shutil

from .config import Config


async def _run(*argv: str, timeout_seconds: float = 5.0) -> dict[str, object]:
    executable = shutil.which(argv[0])
    if executable is None:
        return {"available": False, "command": argv[0]}
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            *argv[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return {
            "available": True,
            "command": argv[0],
            "returncode": None,
            "error": str(exc),
        }
    try:
        output, _ = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError:
        process.kill()
        output, _ = await process.communicate()
        return {
            "available": True,
            "command": argv[0],
            "returncode": process.returncode,
            "timed_out": True,
            "output": output.decode(errors="replace").strip(),
        }
    return {
        "available": True,
        "command": argv[0],
        "returncode": process.returncode,
        "output": output.decode(errors="replace").strip(),
    }


async def diagnose(config: Config | None = None) -> int:
    report = {
        "capture_devices": await _run("arecord", "-l"),
        "playback_devices": await _run("aplay", "-l"),
        "loopback": await _run("alsaloop", "--help"),
        "sendspin": await _run("sendspin", "--help"),
    }
    required = [report["capture_devices"], report["playback_devices"]]
    if config is not None:
        audio = config.audio
        report["configured_capture"] = await _run(
            "arecord", "-q", "-D", audio.capture_device,
            "-t", "raw", "-f", "S16_LE", "-r", str(audio.sample_rate),
            "-c", str(audio.channels), "-d", "1", "/dev/null",
            timeout_seconds=3.0,
        )
        report["configured_playback"] = await _run(
            "aplay", "-q", "-D", audio.playback_device,
            "-t", "raw", "-f", "S16_LE", "-r", str(audio.sample_rate),
            "-c", str(audio.channels), "-d", "1", "/dev/zero",
            timeout_seconds=3.0,
        )
        required.extend(
            [report["configured_capture"], report["configured_playback"]]
        )
    print(json.dumps(report, indent=2))
    healthy = all(
        item.get("available") and item.get("returncode") == 0
        for item in required
    )
    return 0 if healthy else 1
