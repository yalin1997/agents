"""Deterministic adapters for contract and integration tests.

These helpers intentionally avoid provider SDKs and filesystem side effects so
tests can exercise the public boundaries without credentials or a network.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .contracts import ModelRef, ToolCall
from .errors import SessionError


class FakeModel:
    """Return a finite sequence of responses and record every request."""

    def __init__(self, responses: Iterable[Any] = (), *, model_ref: ModelRef | None = None) -> None:
        self._responses = list(responses)
        self.model_ref = model_ref or ModelRef(provider="fake", model_id="fake-model")
        self.requests: list[Any] = []

    def invoke(self, request: Any, **kwargs: Any) -> Any:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("FakeModel received more requests than configured responses")
        response = self._responses.pop(0)
        return response(request) if callable(response) else response


@dataclass
class FakeTool:
    """A named tool whose calls are captured as :class:`ToolCall` values."""

    name: str
    handler: Callable[[Mapping[str, Any]], Any] | None = None
    calls: list[ToolCall] = field(default_factory=list)

    def invoke(self, arguments: Mapping[str, Any], *, context: Any = None) -> Any:
        call = ToolCall(call_id=f"call-{len(self.calls) + 1}", name=self.name, arguments=arguments)
        self.calls.append(call)
        if self.handler is None:
            return None
        return self.handler(dict(call.arguments))


@dataclass
class _MemorySession:
    metadata: dict[str, Any]
    entries: list[dict[str, Any]] = field(default_factory=list)


class InMemorySessionStore:
    """Small append-only store implementing the M0 session boundary."""

    def __init__(self) -> None:
        self._sessions: dict[str, _MemorySession] = {}

    def create(self, session_id: str, metadata: Mapping[str, Any] | None = None) -> None:
        if session_id in self._sessions:
            raise SessionError(f"Session already exists: {session_id}")
        self._sessions[session_id] = _MemorySession(self._copy_mapping(metadata or {}))

    def append(self, session_id: str, entry: Mapping[str, Any], *, expected_version: int | None = None) -> int:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionError(f"Session not found: {session_id}")
        current_version = len(session.entries)
        if expected_version is not None and expected_version != current_version:
            raise SessionError(
                f"Session version conflict: expected {expected_version}, current {current_version}",
                details={"session_id": session_id, "expected_version": expected_version, "current_version": current_version},
                retryable=True,
            )
        session.entries.append(self._copy_mapping(entry))
        return current_version + 1

    def load(self, session_id: str) -> tuple[dict[str, Any], ...]:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionError(f"Session not found: {session_id}")
        return tuple(self._copy_mapping(entry) for entry in session.entries)

    @staticmethod
    def _copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return json.loads(json.dumps(copy.deepcopy(dict(value)), ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            raise SessionError("Session data must be JSON serializable") from exc


__all__ = ["FakeModel", "FakeTool", "InMemorySessionStore"]
