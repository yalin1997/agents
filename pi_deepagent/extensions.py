"""Extension lifecycle boundary."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ExtensionManager(Protocol):
    """Manage extension handles without exposing core mutable internals."""

    def activate(self, extension: Any) -> Any: ...

    def deactivate(self, extension_id: str) -> None: ...

    def reload(self) -> None: ...


__all__ = ["ExtensionManager"]
