"""Capability vocabulary shared by model, tool, and extension adapters."""

from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum
from typing import Protocol, runtime_checkable


class Capability(StrEnum):
    """Named abilities that can be inspected or governed by policy."""

    MODEL = "model"
    TOOL = "tool"
    SESSION = "session"
    EXTENSION = "extension"
    COMMAND = "command"
    CONTEXT = "context"
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    VISION = "vision"
    TOOL_CALLING = "tool_calling"
    THINKING = "thinking"


@runtime_checkable
class CapabilityProvider(Protocol):
    """Adapter contract for exposing capabilities without private internals."""

    @property
    def capabilities(self) -> Collection[Capability]: ...

    def supports(self, capability: Capability) -> bool: ...


__all__ = ["Capability", "CapabilityProvider"]
