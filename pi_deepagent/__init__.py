"""Pi Coding Agent-inspired Deep Agents implementation."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .capabilities import Capability, CapabilityProvider
from .contracts import AgentEvent, AgentRun, EventId, ModelRef, RunId, RunResult, RunStatus, SessionId, ToolCall, ToolCallId
from .errors import (
    CommandError,
    ErrorCode,
    ExtensionError,
    PiDeepAgentError,
    ProviderError,
    RuntimeFailure,
    SessionError,
    ToolError,
    ValidationError,
)
from .runtime import AgentLoop, ApplicationService

def create_pi_deepagent(*args: Any, **kwargs: Any) -> Any:
    """Create a Pi-inspired Deep Agent."""
    return import_module("pi_deepagent.agent").create_pi_deepagent(*args, **kwargs)


__all__ = [
    "AgentEvent",
    "AgentLoop",
    "AgentRun",
    "ApplicationService",
    "Capability",
    "CapabilityProvider",
    "CommandError",
    "ErrorCode",
    "EventId",
    "ExtensionError",
    "ModelRef",
    "PiDeepAgentError",
    "ProviderError",
    "RunId",
    "RunResult",
    "RunStatus",
    "RuntimeFailure",
    "SessionError",
    "SessionId",
    "ToolCall",
    "ToolCallId",
    "ToolError",
    "ValidationError",
    "create_pi_deepagent",
]
