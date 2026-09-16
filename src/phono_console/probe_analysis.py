from __future__ import annotations

import json
import statistics
from pathlib import Path


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def analyze(path: Path) -> dict[str, object]:
    records = [json.loads(line) for line in path.read_text().splitlines() if line]
    started = next(record for record in records if record["event"] == "probe_started")
    captures = [record for record in records if record["event"] == "capture"]
    finished = next(
        (record for record in reversed(records) if record["event"] == "probe_finished"),
        None,
    )
    if len(captures) < 2:
        raise ValueError(f"{path}: probe has fewer than two capture blocks")
    errors = [abs(int(record["delta_error_us"])) for record in captures[1:]]
    expected_span = sum(int(record["expected_delta_us"]) for record in captures[1:])
    actual_span = (
        int(captures[-1]["first_sample_time_us"])
        - int(captures[0]["first_sample_time_us"])
    )
    drift_ppm = (
        (actual_span - expected_span) * 1_000_000 / expected_span
        if expected_span
        else 0.0
    )
    timestamps = [int(record["first_sample_time_us"]) for record in captures]
    return {
        "file": str(path),
        "device": started["device"],
        "rate_hz": started["rate_hz"],
        "channels": started["channels"],
        "period_frames": started["period_frames"],
        "buffer_frames": started["buffer_frames"],
        "blocks": len(captures),
        "frames": sum(int(record["frames"]) for record in captures),
        "xruns": max(int(record["xruns"]) for record in captures),
        "finished": finished is not None,
        "captured_seconds": round(
            sum(int(record["frames"]) for record in captures)
            / int(started["rate_hz"]),
            3,
        ),
        "timestamps_monotonic": all(
            current > previous
            for previous, current in zip(timestamps, timestamps[1:], strict=False)
        ),
        "delta_error_us": {
            "mean_abs": round(statistics.fmean(errors), 2),
            "p50_abs": percentile(errors, 0.50),
            "p95_abs": percentile(errors, 0.95),
            "p99_abs": percentile(errors, 0.99),
            "max_abs": max(errors),
        },
        "span_error_us": actual_span - expected_span,
        "span_drift_ppm": round(drift_ppm, 3),
    }
