import asyncio
import importlib.util
import logging
from pathlib import Path
import queue
from types import SimpleNamespace

from phono_console.playback_diagnostics import PlaybackDiagnostics, alsa_snapshot
from phono_console.simulation import SimulatedEventSink
from phono_console.state import StateStore


def test_alsa_snapshot_reads_owned_handles_and_never_returns_command_arguments(tmp_path):
    pcm = tmp_path / "asound/card2/pcm0p/sub0"
    pcm.mkdir(parents=True)
    (pcm / "status").write_text(
        "state: RUNNING\nowner_pid: 123\ndelay: 4800\nhw_ptr: 9600\nappl_ptr: 14400\n"
    )
    (pcm / "hw_params").write_text("rate: 48000 (48000/1)\nbuffer_size: 8192\n")
    process = tmp_path / "123"
    process.mkdir()
    (process / "cmdline").write_bytes(b"python\0/opt/bin/sendspin-player.py\0secret\0")
    sample = alsa_snapshot(None, tmp_path)
    assert len(sample) == 1
    assert sample[0]["role"] == "sendspin_player"
    assert sample[0]["delay_ms"] == 100
    assert "secret" not in repr(sample)
    assert alsa_snapshot(123, tmp_path)[0]["role"] == "ma_loopback"
    (process / "cmdline").write_bytes(b"unrelated-app\0")
    assert alsa_snapshot(None, tmp_path) == []


def test_missing_or_closed_alsa_status_is_nonfatal(tmp_path):
    assert alsa_snapshot(123, tmp_path) == []
    pcm = tmp_path / "asound/card2/pcm1c/sub0"
    pcm.mkdir(parents=True)
    (pcm / "status").write_text("closed\n")
    assert alsa_snapshot(123, tmp_path) == []


def test_timing_changes_and_heartbeat_are_recorded_without_restarting_audio():
    async def scenario():
        events = SimulatedEventSink()
        state = StateStore()
        loopback = SimpleNamespace(pid=123, running=True)
        diagnostics = PlaybackDiagnostics(state, loopback, events)
        sample = {
            "pcm": "card2/pcm1c/sub0", "owner_pid": "123", "state": "RUNNING",
            "trigger_time": "1", "delay_ms": 40, "hw_ptr": "1000",
        }
        await diagnostics.observe([sample], elapsed=1, wake_late_ms=0)
        await diagnostics.observe([sample], elapsed=2, wake_late_ms=0)
        assert len(events.events) == 1
        await diagnostics.observe(
            [{**sample, "delay_ms": 540, "hw_ptr": "100"}], elapsed=3, wake_late_ms=250
        )
        changes = events.events[-1][1]["changes"]
        assert {c["reason"] for c in changes} == {"buffer_delay_jump", "hw_ptr_decreased"}
        assert state.components["ma_return_diagnostics"]["wake_late_ms"] == 250
        await diagnostics.observe([], elapsed=4, wake_late_ms=0)
        assert events.events[-1][1]["changes"][0]["reason"] == "handle_disappeared"
        assert state.components["ma_return_diagnostics"]["status"] == "degraded"
        assert loopback.running and loopback.pid == 123

    asyncio.run(scenario())


def player_wrapper():
    path = Path(__file__).parents[1] / "scripts/sendspin-player.py"
    spec = importlib.util.spec_from_file_location("diagnostic_player", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_player_audio_logging_is_bounded_and_excludes_protocol_messages(tmp_path):
    wrapper = player_wrapper()
    path = tmp_path / "audio.log"
    close = wrapper.configure_diagnostics(path)
    logging.getLogger("sendspin.audio").debug("Sync error: 500 ms, buffer: 0.5 s")
    logging.getLogger("aiosendspin.client.connection").warning("protocol-secret-test")
    close()
    text = path.read_text()
    assert "Sync error: 500 ms" in text
    assert "protocol-secret-test" not in text
    handler = wrapper.BoundedQueueHandler(queue.Queue(maxsize=1))
    record = logging.makeLogRecord({"name": "sendspin.audio", "msg": "test"})
    handler.enqueue(record)
    handler.enqueue(record)
    assert handler.dropped == 1
    assert handler.queue.qsize() == 1


def test_player_still_runs_if_diagnostic_storage_is_unavailable(tmp_path):
    wrapper = player_wrapper()
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    close = wrapper.configure_diagnostics(blocked / "audio.log")
    close()


def test_player_wrapper_preserves_entrypoint_and_cleans_up(monkeypatch):
    wrapper = player_wrapper()
    actions = []
    monkeypatch.setattr(wrapper, "configure_diagnostics", lambda: lambda: actions.append("closed"))
    monkeypatch.setattr(wrapper, "entry_points", lambda **kwargs: [SimpleNamespace(
        load=lambda: lambda: actions.append("player") or 7,
    )])
    assert wrapper.main() == 7
    assert actions == ["player", "closed"]
