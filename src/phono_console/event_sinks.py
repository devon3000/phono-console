from __future__ import annotations

from .interfaces import EventSink


class CompositeEventSink:
    def __init__(self, *sinks: EventSink) -> None:
        self.sinks = sinks

    async def emit(self, event: str, details: dict[str, object]) -> None:
        for sink in self.sinks:
            await sink.emit(event, details)

