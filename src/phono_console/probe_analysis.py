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
    sample_positions: list[int] = []
    position = 0
    for record in captures:
        sample_positions.append(position)
        position += int(record["frames"])
    mean_position = statistics.fmean(sample_positions)
    mean_timestamp = statistics.fmean(timestamps)
    position_variance = sum(
        (value - mean_position) ** 2 for value in sample_positions
    )
    slope_us_per_frame = sum(
        (sample - mean_position) * (timestamp - mean_timestamp)
        for sample, timestamp in zip(sample_positions, timestamps, strict=True)
    ) / position_variance
    intercept_us = mean_timestamp - slope_us_per_frame * mean_position
    residuals = [
        round(timestamp - (intercept_us + slope_us_per_frame * sample))
        for sample, timestamp in zip(sample_positions, timestamps, strict=True)
    ]
    abs_residuals = [abs(value) for value in residuals]
    fitted_rate_hz = 1_000_000 / slope_us_per_frame
    nominal_rate_hz = int(started["rate_hz"])
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
        "sample_clock_fit": {
            "rate_hz": round(fitted_rate_hz, 6),
            "rate_error_ppm": round(
                (fitted_rate_hz - nominal_rate_hz)
                * 1_000_000
                / nominal_rate_hz,
                3,
            ),
            "residual_mean_us": round(statistics.fmean(residuals), 3),
            "residual_p95_abs_us": percentile(abs_residuals, 0.95),
            "residual_p99_abs_us": percentile(abs_residuals, 0.99),
            "residual_max_abs_us": max(abs_residuals),
        },
    }
