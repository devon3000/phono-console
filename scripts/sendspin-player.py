"""Run the separate Sendspin installation with bounded audio diagnostics."""

import atexit
import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from importlib.metadata import entry_points
from pathlib import Path
import queue
import sys

AUDIO_LOGGERS = ("sendspin.audio", "sendspin.audio_connector")
LOG_PATH = Path("/var/lib/phono-console/sendspin/playback-diagnostics.log")


class AudioOnly(logging.Filter):
    def filter(self, record):
        return any(
            record.name == name or record.name.startswith(name + ".")
            for name in AUDIO_LOGGERS
        )


class BoundedQueueHandler(QueueHandler):
    """Never block the audio thread or emit an error storm if storage stalls."""

    dropped = 0

    def enqueue(self, record):
        record.diagnostic_drops = self.dropped
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1


def configure_diagnostics(path=LOG_PATH):
    logging.basicConfig(level=logging.INFO)
    handler = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        disk = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=2)
        disk.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s diagnostic_drops=%(diagnostic_drops)d %(message)s"
        ))
        handler = BoundedQueueHandler(queue.Queue(maxsize=256))
        handler.addFilter(AudioOnly())
        # Preserve normal INFO/warning messages in the journal, but send the
        # audio DEBUG statistics only to the bounded diagnostic files.
        original_levels = {
            name: logging.getLogger(name).level for name in AUDIO_LOGGERS
        }
        for existing in logging.getLogger().handlers:
            existing.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        for name in AUDIO_LOGGERS:
            logging.getLogger(name).setLevel(logging.DEBUG)
        listener = QueueListener(handler.queue, disk)
        listener.start()
    except OSError as exc:
        logging.getLogger(__name__).warning("Audio diagnostics unavailable: %s", exc)
        return lambda: None

    def close():
        logging.getLogger().removeHandler(handler)
        # QueueListener.stop uses put_nowait for its sentinel. Drain pending
        # writes first so shutdown cannot fail when the queue was saturated.
        handler.queue.join()
        listener.stop()
        disk.close()
        for name, level in original_levels.items():
            logging.getLogger(name).setLevel(level)
        if handler.dropped:
            logging.getLogger(__name__).warning(
                "Dropped %d diagnostic records (audio was not blocked)", handler.dropped
            )

    return close


def main():
    close = configure_diagnostics()
    atexit.register(close)
    try:
        from sendspin_recovery import install_recovery_patch
        install_recovery_patch()
        command = next(iter(entry_points(group="console_scripts", name="sendspin")))
        return command.load()()
    finally:
        atexit.unregister(close)
        close()


if __name__ == "__main__":
    sys.exit(main())
