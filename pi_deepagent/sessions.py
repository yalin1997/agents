"""Storage boundary for durable and ephemeral sessions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from .contracts import JsonValue, SessionId


@runtime_checkable
class SessionStore(Protocol):
    """Append-oriented store contract; implementations may use SQLite or a service."""

    def create(self, session_id: SessionId | str, metadata: Mapping[str, JsonValue] | None = None) -> None: ...

    def append(
        self,
        session_id: SessionId | str,
        entry: Mapping[str, Any],
        *,
        expected_version: int | None = None,
    ) -> int: ...

    def load(self, session_id: SessionId | str) -> Sequence[Mapping[str, Any]]: ...


__all__ = ["SessionStore"]
