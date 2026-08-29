"""Context/resource loading boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ContextLoader(Protocol):
    """Load framework-neutral context resources for a workspace."""

    def load(self, workspace: str | Path) -> Any: ...


__all__ = ["ContextLoader"]
