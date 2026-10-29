"""Event recording and deterministic replay helpers."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping

from .contracts import AgentEvent
from .errors import ValidationError


class EventRecorder:
    """Collect public events and replay them without a live agent."""

    def __init__(self, events: Iterable[AgentEvent] = ()) -> None:
        self._events: list[AgentEvent] = []
        for event in events:
            self.record(event)

    @property
    def events(self) -> tuple[AgentEvent, ...]:
        return tuple(self._events)

    def record(self, event: AgentEvent) -> None:
        if not isinstance(event, AgentEvent):
            raise TypeError("EventRecorder accepts AgentEvent values")
        self._events.append(event)

    def record_many(self, events: Iterable[AgentEvent]) -> None:
        for event in events:
            self.record(event)

    def to_dict(self) -> list[dict[str, object]]:
        return [event.to_dict() for event in self._events]

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: object) -> EventRecorder:
        if not isinstance(value, list):
            raise ValidationError("events must be an array", details={"field": "events"})
        events: list[AgentEvent] = []
        for item in value:
            if not isinstance(item, Mapping):
                raise ValidationError("each event must be an object", details={"field": "events"})
            events.append(AgentEvent.from_dict(item))
        return cls(events)

    @classmethod
    def from_json(cls, value: str) -> EventRecorder:
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValidationError("events must contain valid JSON") from exc
        return cls.from_dict(decoded)

    def replay(self, consumer: Callable[[AgentEvent], object] | None = None) -> tuple[AgentEvent, ...]:
        if consumer is not None:
            for event in self._events:
                consumer(event)
        return self.events


__all__ = ["EventRecorder"]
