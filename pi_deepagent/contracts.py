"""Framework-neutral domain contracts for the pi_deepagent runtime.

The objects in this module deliberately contain plain Python values only. They
do not depend on LangChain message classes, Deep Agents state, or a particular
storage implementation. ``to_dict`` returns the version-one wire shape and
``to_json`` uses deterministic JSON encoding for fixtures and persistence
adapters.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, NewType, TypeAlias

from .errors import ValidationError


SessionId = NewType("SessionId", str)
RunId = NewType("RunId", str)
EventId = NewType("EventId", str)
ToolCallId = NewType("ToolCallId", str)

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class RunStatus(StrEnum):
    """Terminal states represented by :class:`RunResult`."""

    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string", details={"field": field_name})
    return value


def _copy_json(value: object, field_name: str) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        copied: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{field_name} keys must be strings")
            copied[key] = _copy_json(item, f"{field_name}.{key}")
        return copied
    if isinstance(value, (list, tuple)):
        return [_copy_json(item, field_name) for item in value]
    raise TypeError(f"{field_name} must contain JSON-compatible values, got {type(value).__name__}")


def _copy_mapping(value: Mapping[str, Any] | None, field_name: str) -> dict[str, JsonValue]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValidationError(f"{field_name} must be an object", details={"field": field_name})
    copied = _copy_json(value, field_name)
    assert isinstance(copied, dict)
    return copied


def _normalize_datetime(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValidationError(f"{field_name} must be a datetime", details={"field": field_name})
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _datetime_to_json(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _datetime_from_json(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be an ISO-8601 string", details={"field": field_name})
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be an ISO-8601 string", details={"field": field_name}) from exc
    return _normalize_datetime(parsed, field_name)


def _as_mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{field_name} must be an object", details={"field": field_name})
    return value


@dataclass(frozen=True, slots=True)
class ModelRef:
    """A provider-qualified model identifier independent of any SDK."""

    provider: str
    model_id: str
    thinking_level: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _required_text(self.provider, "provider"))
        object.__setattr__(self, "model_id", _required_text(self.model_id, "model_id"))
        if self.thinking_level is not None:
            object.__setattr__(self, "thinking_level", _required_text(self.thinking_level, "thinking_level"))

    @property
    def qualified_name(self) -> str:
        return f"{self.provider}/{self.model_id}"

    def to_dict(self) -> dict[str, str | None]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "thinking_level": self.thinking_level,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ModelRef:
        data = _as_mapping(value, "model")
        return cls(
            provider=data.get("provider"),
            model_id=data.get("model_id"),
            thinking_level=data.get("thinking_level"),
        )


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A framework-neutral request to invoke a named tool."""

    call_id: ToolCallId | str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "call_id", _required_text(self.call_id, "call_id"))
        object.__setattr__(self, "name", _required_text(self.name, "name"))
        object.__setattr__(self, "arguments", _copy_mapping(self.arguments, "arguments"))

    def to_dict(self) -> dict[str, JsonValue]:
        return {"call_id": self.call_id, "name": self.name, "arguments": dict(self.arguments)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ToolCall:
        data = _as_mapping(value, "tool_call")
        return cls(call_id=data.get("call_id"), name=data.get("name"), arguments=data.get("arguments") or {})


@dataclass(frozen=True, slots=True)
class AgentRun:
    """Metadata for one agent turn."""

    run_id: RunId | str
    session_id: SessionId | str
    model: ModelRef
    workspace: str | Path
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    parent_run_id: RunId | str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(self, "session_id", _required_text(self.session_id, "session_id"))
        if not isinstance(self.model, ModelRef):
            raise ValidationError("model must be a ModelRef", details={"field": "model"})
        if not isinstance(self.workspace, (str, Path)):
            raise ValidationError("workspace must be a string or Path", details={"field": "workspace"})
        workspace = _required_text(str(self.workspace), "workspace")
        object.__setattr__(self, "workspace", Path(workspace).as_posix())
        object.__setattr__(self, "started_at", _normalize_datetime(self.started_at, "started_at"))
        if self.parent_run_id is not None:
            object.__setattr__(self, "parent_run_id", _required_text(self.parent_run_id, "parent_run_id"))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata, "metadata"))

    def to_dict(self) -> dict[str, JsonValue | None]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "model": self.model.to_dict(),
            "workspace": self.workspace,
            "started_at": _datetime_to_json(self.started_at),
            "parent_run_id": self.parent_run_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AgentRun:
        data = _as_mapping(value, "run")
        return cls(
            run_id=data.get("run_id"),
            session_id=data.get("session_id"),
            model=ModelRef.from_dict(_as_mapping(data.get("model"), "model")),
            workspace=data.get("workspace"),
            started_at=_datetime_from_json(data.get("started_at"), "started_at"),
            parent_run_id=data.get("parent_run_id"),
            metadata=data.get("metadata") or {},
        )


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """A sequenced event emitted while a run is executing."""

    event_id: EventId | str
    run_id: RunId | str
    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sequence: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required_text(self.event_id, "event_id"))
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(self, "kind", _required_text(self.kind, "kind"))
        object.__setattr__(self, "payload", _copy_mapping(self.payload, "payload"))
        object.__setattr__(self, "occurred_at", _normalize_datetime(self.occurred_at, "occurred_at"))
        if self.sequence is not None and (not isinstance(self.sequence, int) or self.sequence < 0):
            raise ValidationError("sequence must be a non-negative integer", details={"field": "sequence"})

    def to_dict(self) -> dict[str, JsonValue | None]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "payload": dict(self.payload),
            "occurred_at": _datetime_to_json(self.occurred_at),
            "sequence": self.sequence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AgentEvent:
        data = _as_mapping(value, "event")
        return cls(
            event_id=data.get("event_id"),
            run_id=data.get("run_id"),
            kind=data.get("kind"),
            payload=data.get("payload") or {},
            occurred_at=_datetime_from_json(data.get("occurred_at"), "occurred_at"),
            sequence=data.get("sequence"),
        )


@dataclass(frozen=True, slots=True)
class RunResult:
    """The framework-neutral result of one agent run."""

    run_id: RunId | str
    status: RunStatus | str
    output: str = ""
    events: tuple[AgentEvent, ...] = ()
    error_code: str | None = None
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        try:
            object.__setattr__(self, "status", RunStatus(self.status))
        except ValueError as exc:
            raise ValidationError(f"Unknown run status: {self.status}", details={"field": "status"}) from exc
        if not isinstance(self.output, str):
            raise ValidationError("output must be a string", details={"field": "output"})
        events = tuple(self.events)
        if any(not isinstance(event, AgentEvent) for event in events):
            raise ValidationError("events must contain AgentEvent values", details={"field": "events"})
        object.__setattr__(self, "events", events)
        if self.error_code is not None:
            object.__setattr__(self, "error_code", _required_text(self.error_code, "error_code"))
        if self.error is not None and not isinstance(self.error, str):
            raise ValidationError("error must be a string", details={"field": "error"})
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata, "metadata"))

    def to_dict(self) -> dict[str, JsonValue | None]:
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "output": self.output,
            "events": [event.to_dict() for event in self.events],
            "error_code": self.error_code,
            "error": self.error,
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunResult:
        data = _as_mapping(value, "run_result")
        raw_events = data.get("events") or []
        if not isinstance(raw_events, (list, tuple)):
            raise ValidationError("events must be an array", details={"field": "events"})
        return cls(
            run_id=data.get("run_id"),
            status=data.get("status"),
            output=data.get("output", ""),
            events=tuple(AgentEvent.from_dict(_as_mapping(item, "event")) for item in raw_events),
            error_code=data.get("error_code"),
            error=data.get("error"),
            metadata=data.get("metadata") or {},
        )

    @classmethod
    def from_json(cls, value: str) -> RunResult:
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValidationError("run_result must contain valid JSON") from exc
        return cls.from_dict(_as_mapping(decoded, "run_result"))


__all__ = [
    "AgentEvent",
    "AgentRun",
    "EventId",
    "JsonValue",
    "ModelRef",
    "RunId",
    "RunResult",
    "RunStatus",
    "SessionId",
    "ToolCall",
    "ToolCallId",
    "ValidationError",
]
