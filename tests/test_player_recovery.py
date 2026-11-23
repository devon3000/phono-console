"""PCM-content assertions, not just a reset sync-error counter."""

import importlib.util
from pathlib import Path
import queue
import struct
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/sendspin_recovery.py"
spec = importlib.util.spec_from_file_location("phono_recovery", SCRIPT)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
PlaybackState = SimpleNamespace(PLAYING="playing")


class Player:
    def __init__(self, rate=48000):
        self._format = SimpleNamespace(sample_rate=rate, frame_size=4)
        self._playback_state = PlaybackState.PLAYING
        self._server_ts_cursor_us = 10_000_000
        self._server_ts_cursor_remainder = 0
        self._last_known_playback_position_us = 10_000_000
        self._force_reanchor = False
        self._sync_error_filter = SimpleNamespace(reset=lambda: None)
        self._insert_every_n_frames = self._drop_every_n_frames = 0
        self._frames_until_next_insert = self._frames_until_next_drop = 0
        self._queue = queue.Queue()
        self._current_chunk = None
        self._current_chunk_offset = 0
        self._queued_duration_us = 0
        self._sync_error_filtered_us = 503000

    def feed(self, first, frames):
        self._queue.put(b"".join(struct.pack("<I", i + 1) for i in range(first, first + frames)))
        self._queued_duration_us += frames * 1_000_000 // self._format.sample_rate

    def consume(self, frames):
        result = bytearray()
        while frames:
            if self._current_chunk is None:
                try:
                    self._current_chunk = self._queue.get_nowait()
                    self._current_chunk_offset = 0
                except queue.Empty:
                    break
            available = (len(self._current_chunk) - self._current_chunk_offset) // 4
            take = min(frames, available)
            start = self._current_chunk_offset
            result.extend(self._current_chunk[start:start + take * 4])
            self._current_chunk_offset += take * 4
            remainder = self._server_ts_cursor_remainder + take * 1_000_000
            self._server_ts_cursor_us += remainder // self._format.sample_rate
            self._server_ts_cursor_remainder = remainder % self._format.sample_rate
            frames -= take
            if self._current_chunk_offset == len(self._current_chunk):
                self._queued_duration_us -= len(self._current_chunk) // 4 * 1_000_000 // self._format.sample_rate
                self._current_chunk = None
                self._current_chunk_offset = 0
        return bytes(result)

    def _skip_input_frames(self, frames):
        self.consume(frames)

    @staticmethod
    def _fill_silence(output, offset, size):
        output[offset:offset + size] = b"\0" * size

    def _audio_callback(self, outdata, frames, time=None, status=None):
        bytes_needed = frames * self._format.frame_size
        output_buffer = memoryview(outdata)
        if (
            self._playback_state == PlaybackState.PLAYING
            and self._last_known_playback_position_us > 0
            and self._server_ts_cursor_us > 0
            and self._force_reanchor
        ):
            self._server_ts_cursor_us = self._last_known_playback_position_us
            self._server_ts_cursor_remainder = 0
            self._force_reanchor = False
            self._insert_every_n_frames = 0
            self._drop_every_n_frames = 0
            self._frames_until_next_insert = 0
            self._frames_until_next_drop = 0
            self._sync_error_filter.reset()
            self._sync_error_filtered_us = 0.0
        bytes_written = 0
        data = self.consume(frames)
        output_buffer[bytes_written:bytes_written + len(data)] = data

    def _update_correction_schedule(self, error_us):
        if abs(error_us) > 500000:
            self._force_reanchor = True

    def clear(self):
        self._force_reanchor = True

    def set_format(self, fmt, device):
        self.clear()


def player(*, patched=True, rate=48000):
    # A fresh class keeps the unpatched regression comparison available.
    class Subject(Player):
        pass
    if patched:
        recovery.patch_player_class(Subject)
    return Subject(rate)


def render(subject, frames=2048):
    out = bytearray(frames * 4)
    subject._audio_callback(out, frames)
    return out


def test_old_reset_reports_success_without_advancing_audio():
    subject = player(patched=False)
    subject.feed(0, 48000)
    subject._last_known_playback_position_us += 503000
    subject._update_correction_schedule(503000)
    assert struct.unpack_from("<I", render(subject))[0] == 1
    assert subject._sync_error_filtered_us == 0  # But this is still old PCM!


@pytest.mark.parametrize("rate", [44100, 48000])
def test_behind_recovery_advances_real_pcm_across_partial_chunks(rate):
    subject = player(rate=rate)
    for first in range(0, rate * 2, rate // 100):
        subject.feed(first, rate // 100)
    subject.consume(13)  # Recover from a partially consumed chunk.
    before = subject._server_ts_cursor_us
    subject._last_known_playback_position_us = before + 503000
    subject._update_correction_schedule(503000)
    out = render(subject)
    skipped = (503000 * rate + 999999) // 1_000_000
    assert struct.unpack_from("<I", out)[0] == 14 + skipped
    assert not subject._force_reanchor
    assert subject._queued_duration_us < 2_000_000


def test_ahead_recovery_waits_without_consuming_future_audio():
    subject = player()
    subject.feed(0, 48000)
    subject._last_known_playback_position_us -= 503000
    subject._update_correction_schedule(-503000)
    assert render(subject) == bytes(2048 * 4)
    assert subject._server_ts_cursor_us == 10_000_000
    assert subject._current_chunk is None
    assert subject._force_reanchor
    # Last callback of the wait has a 480-frame gap, then real PCM.
    subject._last_known_playback_position_us = 9_990_000
    out = render(subject)
    assert out[:480 * 4] == bytes(480 * 4)
    assert struct.unpack_from("<I", out, 480 * 4)[0] == 1
    assert not subject._force_reanchor
    assert subject._server_ts_cursor_us == 10_000_000 + (2048 - 480) * 1_000_000 // 48000


def test_empty_queue_cannot_be_relabelled_as_successful_recovery():
    subject = player()
    subject.feed(0, 4800)
    subject._last_known_playback_position_us += 503000
    subject._update_correction_schedule(503000)
    assert render(subject) == bytes(2048 * 4)
    assert subject._force_reanchor
    assert subject._server_ts_cursor_us == 10_100_000
    subject.feed(4800, 48000)
    out = render(subject)
    assert struct.unpack_from("<I", out)[0] == 24145
    assert not subject._force_reanchor


def test_startup_anchor_and_explicit_clear_are_not_recovery():
    subject = player()
    subject.feed(0, 48000)
    subject._last_known_playback_position_us += 180000
    subject._force_reanchor = True  # Original startup and static-delay anchor.
    assert struct.unpack_from("<I", render(subject))[0] == 1
    subject._phono_pcm_recovery = True
    subject.clear()
    assert not subject._phono_pcm_recovery
    subject._phono_pcm_recovery = True
    subject.set_format(None, None)
    assert not subject._phono_pcm_recovery


def test_patch_is_idempotent_and_unrecognized_code_fails_before_mutation():
    subject = player()
    original = type(subject)._audio_callback
    recovery.patch_player_class(type(subject))
    assert type(subject)._audio_callback is original

    class Changed(Player):
        def _update_correction_schedule(self, error_us):
            pass
    before = Changed._audio_callback
    with pytest.raises(RuntimeError, match="does not match"):
        recovery.patch_player_class(Changed)
    assert Changed._audio_callback is before
