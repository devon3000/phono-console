from __future__ import annotations

import json
import logging


class LoggingEventSink:
    """Emit machine-readable events locally; MA forwarding wraps this sink later."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("phono_console.events")

    async def emit(self, event: str, details: dict[str, object]) -> None:
        self.logger.info(
            "%s",
            json.dumps({"event": event, **details}, sort_keys=True, default=str),
        )

