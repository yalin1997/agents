"""Pi Coding Agent-inspired Deep Agents implementation."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .capabilities import Capability, CapabilityProvider
from .contracts import (
    AgentEvent,
    AgentRequest,
    AgentRun,
    EventId,
    ModelRef,
    RunId,
    RunRequest,
    RunResult,
    RunStatus,
    SessionId,
    ToolCall,
    ToolCallId,
)
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
from .events import EventRecorder
from .runtime import AgentLoop, AgentLoopRuntime, ApplicationService, RetryPolicy

def create_pi_deepagent(*args: Any, **kwargs: Any) -> Any:
    """Create a Pi-inspired Deep Agent."""
    return import_module("pi_deepagent.agent").create_pi_deepagent(*args, **kwargs)


__all__ = [
    "AgentEvent",
    "AgentRequest",
    "AgentLoop",
    "AgentLoopRuntime",
    "AgentRun",
    "ApplicationService",
    "Capability",
    "CapabilityProvider",
    "CommandError",
    "ErrorCode",
    "EventRecorder",
    "EventId",
    "ExtensionError",
    "ModelRef",
    "PiDeepAgentError",
    "ProviderError",
    "RunId",
    "RunRequest",
    "RunResult",
    "RunStatus",
    "RetryPolicy",
    "RuntimeFailure",
    "SessionError",
    "SessionId",
    "ToolCall",
    "ToolCallId",
    "ToolError",
    "ValidationError",
    "create_pi_deepagent",
]
