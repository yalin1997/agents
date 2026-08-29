"""Stable error primitives shared by the public pi_deepagent contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping


class ErrorCode(StrEnum):
    """Machine-readable error codes exposed by the application boundary."""

    VALIDATION = "validation_error"
    PROVIDER = "provider_error"
    TOOL = "tool_error"
    SESSION = "session_error"
    EXTENSION = "extension_error"
    COMMAND = "command_error"
    RUNTIME = "runtime_error"
    INTERNAL = "internal_error"


class PiDeepAgentError(Exception):
    """Base class for errors that may cross a public application boundary."""

    code = ErrorCode.INTERNAL
    default_retryable = False

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})
        self.retryable = self.default_retryable if retryable is None else retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "details": dict(self.details),
            "retryable": self.retryable,
        }


class ValidationError(PiDeepAgentError):
    """Raised when a domain contract contains invalid data."""

    code = ErrorCode.VALIDATION


class ProviderError(PiDeepAgentError):
    code = ErrorCode.PROVIDER


class ToolError(PiDeepAgentError):
    code = ErrorCode.TOOL


class SessionError(PiDeepAgentError):
    code = ErrorCode.SESSION


class ExtensionError(PiDeepAgentError):
    code = ErrorCode.EXTENSION


class CommandError(PiDeepAgentError):
    code = ErrorCode.COMMAND


class RuntimeFailure(PiDeepAgentError):
    code = ErrorCode.RUNTIME


__all__ = [
    "CommandError",
    "ErrorCode",
    "ExtensionError",
    "PiDeepAgentError",
    "ProviderError",
    "RuntimeFailure",
    "SessionError",
    "ToolError",
    "ValidationError",
]
