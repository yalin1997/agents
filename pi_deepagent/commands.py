"""Slash-command registry boundary shared by frontends."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CommandRegistry(Protocol):
    """Register and dispatch commands independently of CLI input handling."""

    def register(self, command: Any) -> None: ...

    def dispatch(self, text: str, context: Any = None) -> Any: ...


__all__ = ["CommandRegistry"]
