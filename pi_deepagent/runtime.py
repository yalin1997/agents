"""Application and runtime boundaries for pi_deepagent."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any, Protocol, runtime_checkable

from .contracts import AgentEvent, RunResult


@runtime_checkable
class AgentInvoker(Protocol):
    """Minimal adapter required by the application service."""

    def invoke(self, state: Mapping[str, object]) -> Mapping[str, object]: ...


@runtime_checkable
class AgentLoop(Protocol):
    """Async-first boundary implemented by the runtime loop milestone."""

    async def run(self, request: object) -> RunResult: ...

    def stream(self, request: object) -> AsyncIterator[AgentEvent]: ...


def message_content(message: Any) -> str:
    """Extract printable text without making domain contracts SDK-specific."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return "" if content is None else str(content)


def last_assistant_message(messages: Iterable[Any]) -> Any:
    materialized = list(messages)
    for message in reversed(materialized):
        if getattr(message, "type", None) == "ai":
            return message
    return materialized[-1] if materialized else ""


class ApplicationService:
    """Own conversation state shared by CLI, SDK, and future UI adapters."""

    def __init__(self, agent: AgentInvoker, history: Iterable[Any] | None = None) -> None:
        self._agent = agent
        self._history = list(history or [])

    @property
    def history(self) -> tuple[Any, ...]:
        """Return an immutable snapshot of the current conversation history."""
        return tuple(self._history)

    def ask(self, prompt: str) -> str:
        """Send one prompt, append the returned message history, and return text."""
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        result = self._agent.invoke({"messages": [*self._history, prompt]})
        messages = result.get("messages") if isinstance(result, Mapping) else None
        if not isinstance(messages, list):
            raise RuntimeError("Agent returned no message history")
        self._history = messages
        return message_content(last_assistant_message(messages))

    def reset(self) -> None:
        """Clear the in-memory conversation owned by this application service."""
        self._history.clear()


def ask_with_history(agent: AgentInvoker, prompt: str, history: Iterable[Any]) -> tuple[list[Any], str]:
    """Compatibility adapter for callers of the pre-M0 helper shape."""
    service = ApplicationService(agent, history)
    answer = service.ask(prompt)
    return list(service.history), answer


__all__ = [
    "AgentInvoker",
    "AgentLoop",
    "ApplicationService",
    "ask_with_history",
    "last_assistant_message",
    "message_content",
]
