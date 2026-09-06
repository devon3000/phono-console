from __future__ import annotations

import asyncio
import json
import shutil


async def _run(*argv: str) -> dict[str, object]:
    executable = shutil.which(argv[0])
    if executable is None:
        return {"available": False, "command": argv[0]}
    process = await asyncio.create_subprocess_exec(
        executable,
        *argv[1:],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    return {
        "available": True,
        "command": argv[0],
        "returncode": process.returncode,
        "output": output.decode(errors="replace").strip(),
    }


async def diagnose() -> int:
    report = {
        "capture_devices": await _run("arecord", "-l"),
        "playback_devices": await _run("aplay", "-l"),
        "loopback": await _run("alsaloop", "--help"),
        "sendspin": await _run("sendspin", "--help"),
    }
    print(json.dumps(report, indent=2))
    required = (report["capture_devices"], report["playback_devices"])
    return 0 if all(item["available"] for item in required) else 1

