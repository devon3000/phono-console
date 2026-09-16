import json
from pathlib import Path

from phono_console.probe_analysis import analyze


def test_probe_summary_reports_sample_timeline_error(tmp_path: Path) -> None:
    records = [
        {
            "event": "probe_started",
            "device": "bluealsa",
            "rate_hz": 48000,
            "channels": 2,
            "period_frames": 960,
            "buffer_frames": 3840,
        },
        *[
            {
                "event": "capture",
                "sequence": sequence,
                "frames": 960,
                "first_sample_time_us": 1_000_000 + sequence * 20_000,
                "expected_delta_us": 0 if sequence == 0 else 20_000,
                "delta_error_us": 0,
                "xruns": 0,
            }
            for sequence in range(3)
        ],
        {"event": "probe_finished", "blocks": 3, "frames": 2880, "xruns": 0},
    ]
    path = tmp_path / "probe.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records))

    summary = analyze(path)

    assert summary["timestamps_monotonic"] is True
    assert summary["captured_seconds"] == 0.06
    assert summary["span_drift_ppm"] == 0
    assert summary["delta_error_us"]["max_abs"] == 0
