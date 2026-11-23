"""Version-checked recovery fix for Sendspin 7.5.0's cursor-only reanchor.

Keep startup DAC anchoring (including the configured static delay) unchanged.
Only a large-error recovery must move PCM along with its timestamp cursor.
"""

import inspect
from importlib.metadata import version
import logging
import textwrap

LOGGER = logging.getLogger("sendspin.audio")


def recover_pcm(player, callback_frames):
    """Return (silence_prefix_frames, still_waiting) without relabeling PCM."""
    rate = player._format.sample_rate
    error_us = player._last_known_playback_position_us - player._server_ts_cursor_us
    if error_us > 0:
        frames = (error_us * rate + 999_999) // 1_000_000
        # The upstream skip routine updates partial-chunk offsets, duration,
        # cursor and fractional remainder, and stops safely on an empty queue.
        player._skip_input_frames(frames)
        remaining = player._last_known_playback_position_us - player._server_ts_cursor_us
        return 0, remaining * rate >= 1_000_000
    if error_us < 0:
        frames = ((-error_us) * rate + 999_999) // 1_000_000
        # Do not consume or timestamp-shift future audio. The current DAC time
        # is refreshed on every callback while we wait for its presentation.
        return min(callback_frames, frames), frames >= callback_frames
    return 0, False


OLD_ANCHOR = """\
        self._server_ts_cursor_us = self._last_known_playback_position_us
        self._server_ts_cursor_remainder = 0
"""

NEW_ANCHOR = """\
        if getattr(self, "_phono_pcm_recovery", False):
            recovery_silence, pending = _phono_recover_pcm(self, frames)
            if pending:
                self._fill_silence(output_buffer, 0, bytes_needed)
                return
            self._phono_pcm_recovery = False
        else:
            # Initial DAC anchoring is not a mid-stream recovery.
            self._server_ts_cursor_us = self._last_known_playback_position_us
            self._server_ts_cursor_remainder = 0
"""


def _replace_once(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError("Sendspin recovery patch does not match installed source")
    return source.replace(old, new, 1)


def _compile_method(original, source):
    scope = dict(original.__globals__)
    scope["_phono_recover_pcm"] = recover_pcm
    exec(compile("from __future__ import annotations\n" + source,
                 "<phono-sendspin-recovery>", "exec"), scope)
    return scope[original.__name__]


def patch_player_class(player_class):
    """Validate all edits before replacing any method; no disk/package edits."""
    if getattr(player_class, "_phono_pcm_recovery_patched", False):
        return
    callback = player_class._audio_callback
    source = textwrap.dedent(inspect.getsource(callback))
    source = _replace_once(source, OLD_ANCHOR, NEW_ANCHOR)
    source = _replace_once(
        source, "    bytes_written = 0\n",
        "    bytes_written = recovery_silence * self._format.frame_size\n"
        "    if bytes_written:\n"
        "        self._fill_silence(output_buffer, 0, bytes_written)\n"
        "    frames -= recovery_silence\n",
    )
    source = _replace_once(
        source, "    bytes_needed = frames * self._format.frame_size\n",
        "    recovery_silence = 0\n"
        "    bytes_needed = frames * self._format.frame_size\n",
    )
    new_callback = _compile_method(callback, source)
    correction = player_class._update_correction_schedule
    source = textwrap.dedent(inspect.getsource(correction))
    source = _replace_once(
        source, "        self._force_reanchor = True\n",
        "        self._phono_pcm_recovery = True\n"
        "        self._force_reanchor = True\n",
    )
    new_correction = _compile_method(correction, source)

    def reset_wrapper(original):
        def reset(self, *args, **kwargs):
            self._phono_pcm_recovery = False
            return original(self, *args, **kwargs)
        return reset

    player_class._audio_callback = new_callback
    player_class._update_correction_schedule = new_correction
    player_class.clear = reset_wrapper(player_class.clear)
    player_class.set_format = reset_wrapper(player_class.set_format)
    player_class._phono_pcm_recovery_patched = True


def install_recovery_patch():
    installed = version("sendspin")
    if installed != "7.5.0":
        raise RuntimeError(f"PCM recovery fix requires sendspin 7.5.0, got {installed}")
    from sendspin.audio import AudioPlayer
    patch_player_class(AudioPlayer)
    LOGGER.info("PCM-aware reanchor recovery enabled for Sendspin %s", installed)
