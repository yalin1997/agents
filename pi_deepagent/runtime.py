"""Application and runtime boundaries for pi_deepagent."""

from __future__ import annotations

import asyncio
import inspect
from collections import deque
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from .contracts import AgentEvent, AgentRequest, AgentRun, ModelRef, RunResult, RunStatus, ToolCall
from .errors import PiDeepAgentError, ProviderError
from .events import EventRecorder


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
    if isinstance(message, Mapping):
        content = message.get("content", message.get("text", ""))
    else:
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


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry policy for transient provider failures."""

    max_attempts: int = 1
    backoff_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must not be negative")

    def should_retry(self, error: BaseException, attempt: int) -> bool:
        if not isinstance(error, ProviderError) or not error.retryable or attempt >= self.max_attempts:
            return False
        side_effect_started = any(
            bool(error.details.get(key))
            for key in ("side_effect_started", "side_effects_started", "tool_side_effect")
        )
        return not side_effect_started

    def delay_for(self, attempt: int) -> float:
        return self.backoff_seconds * (2 ** max(attempt - 1, 0))


class AgentLoopRuntime:
    """Async-first adapter around a Deep Agents/LangGraph-compatible graph."""

    def __init__(
        self,
        agent: Any,
        *,
        model: ModelRef,
        workspace: str | Path,
        session_id: str | None = None,
        retry_policy: RetryPolicy | None = None,
        recorder: EventRecorder | None = None,
    ) -> None:
        self._agent = agent
        self._model = model
        self._workspace = Path(workspace).as_posix()
        self._session_id = session_id or uuid4().hex
        self._retry_policy = retry_policy or RetryPolicy()
        self._recorder = recorder
        self._pending_messages: deque[str] = deque()
        self._message_history: list[Any] = []
        self._active_task: asyncio.Task[Any] | None = None
        self._abort_requested = False

    @property
    def pending_messages(self) -> tuple[str, ...]:
        """Return queued follow-up/unsupported-steering messages."""
        return tuple(self._pending_messages)

    @property
    def message_history(self) -> tuple[Any, ...]:
        """Return the last graph message state used for the next turn."""
        return tuple(self._message_history)

    def reset_history(self) -> None:
        """Clear message state without affecting queued controls."""
        self._message_history.clear()

    def steer(self, message: str) -> None:
        """Send steering to an active steerable graph or queue it safely."""
        self._validate_control_message(message)
        if self._active_task is not None and not self._active_task.done():
            steer = getattr(self._agent, "steer", None)
            if callable(steer):
                result = steer(message)
                if inspect.isawaitable(result):
                    asyncio.create_task(result)
                return
        self._pending_messages.append(message)

    def follow_up(self, message: str) -> None:
        """Queue a message for the next run when the current run completes."""
        self._validate_control_message(message)
        self._pending_messages.append(message)

    def abort(self, *, discard_queued: bool = False) -> None:
        """Request cancellation, optionally dropping queued messages."""
        if discard_queued:
            self._pending_messages.clear()
        self._abort_requested = True
        task = self._active_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def run(self, request: str | AgentRequest | None = None) -> RunResult:
        """Execute one request and return a framework-neutral terminal result."""
        if request is None:
            if not self._pending_messages:
                raise ValueError("request is required when no message is queued")
            request = self._pending_messages.popleft()
        prompt, session_id, model, workspace, parent_run_id, request_metadata = self._resolve_request(request)

        run = AgentRun(
            run_id=uuid4().hex,
            session_id=session_id,
            model=model,
            workspace=workspace,
            parent_run_id=parent_run_id,
            metadata=request_metadata,
        )
        context = self._run_context(run)
        self._claim_active()
        retry_events: list[AgentEvent] = []
        state = {"messages": [*self._message_history, prompt]}
        start = self._publish(AgentEvent(
            event_id=uuid4().hex,
            run_id=run.run_id,
            kind="run_start",
            payload={
                "run_id": run.run_id,
                "session_id": run.session_id,
                "model": run.model.to_dict(),
                "workspace": run.workspace,
                "reasoning_level": run.model.thinking_level,
                "parent_run_id": run.parent_run_id,
                "metadata": dict(run.metadata),
            },
            sequence=0,
        ))
        try:
            if self._abort_requested:
                return self._aborted_result(run, start)
            attempt = 1
            while True:
                try:
                    raw_result = await self._invoke(state, context=context)
                    output = self._output_from_result(raw_result)
                    self._capture_history(raw_result)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - policy classifies adapter failures
                    if not self._retry_policy.should_retry(exc, attempt):
                        error_code = self._error_code(exc)
                        end = self._publish(AgentEvent(
                            event_id=uuid4().hex,
                            run_id=run.run_id,
                            kind="error",
                            payload={"message": str(exc), "error_code": error_code},
                            sequence=len(retry_events) + 1,
                        ))
                        return RunResult(
                            run_id=run.run_id,
                            status=RunStatus.FAILED,
                            events=(start, *retry_events, end),
                            error_code=error_code,
                            error=str(exc),
                            metadata=context,
                        )
                    retry_events.append(
                        self._publish(AgentEvent(
                            event_id=uuid4().hex,
                            run_id=run.run_id,
                            kind="retry",
                            payload={
                                "attempt": attempt,
                                "next_attempt": attempt + 1,
                                "error_code": self._error_code(exc),
                            },
                            sequence=len(retry_events) + 1,
                        ))
                    )
                    await asyncio.sleep(self._retry_policy.delay_for(attempt))
                    attempt += 1
        except asyncio.CancelledError:
            if self._abort_requested:
                return self._aborted_result(run, start)
            raise
        finally:
            if self._active_task is asyncio.current_task():
                self._active_task = None
            self._abort_requested = False

        end = self._publish(AgentEvent(
            event_id=uuid4().hex,
            run_id=run.run_id,
            kind="run_end",
            payload={"status": RunStatus.COMPLETED.value, "output": output},
            sequence=len(retry_events) + 1,
        ))
        return RunResult(
            run_id=run.run_id,
            status=RunStatus.COMPLETED,
            output=output,
            events=(start, *retry_events, end),
            metadata=context,
        )

    async def stream(self, request: str | AgentRequest) -> AsyncIterator[AgentEvent]:
        """Yield normalized lifecycle, model, and tool events."""
        prompt, session_id, model, workspace, parent_run_id, request_metadata = self._resolve_request(request)

        run = AgentRun(
            run_id=uuid4().hex,
            session_id=session_id,
            model=model,
            workspace=workspace,
            parent_run_id=parent_run_id,
            metadata=request_metadata,
        )
        context = self._run_context(run)
        self._claim_active()
        start = self._publish(AgentEvent(
            event_id=uuid4().hex,
            run_id=run.run_id,
            kind="run_start",
            payload={
                "run_id": run.run_id,
                "session_id": run.session_id,
                "model": run.model.to_dict(),
                "workspace": run.workspace,
                "reasoning_level": run.model.thinking_level,
                "parent_run_id": run.parent_run_id,
                "metadata": dict(run.metadata),
            },
            sequence=0,
        ))
        sequence = 1
        model_started = False
        output_parts: list[str] = []
        final_output = ""
        attempt = 1
        tool_side_effect_started = False
        try:
            yield start
            while True:
                try:
                    state = {"messages": [*self._message_history, prompt]}
                    history_captured = False
                    native_stream = self._native_event_stream(state, context=context)
                    event_stream = native_stream is not None
                    if native_stream is None:
                        native_stream = self._native_stream(state, context=context)
                    if native_stream is not None:
                        if inspect.isawaitable(native_stream):
                            native_stream = await native_stream
                        async for chunk in native_stream:
                            if event_stream:
                                history_captured = self._capture_event_history(chunk) or history_captured
                            normalized = self._normalize_callback_event(chunk) if event_stream else self._normalize_chunk(chunk)
                            for kind, payload in normalized:
                                if kind == "run_start":
                                    continue
                                if kind == "run_end":
                                    candidate = payload.get("output")
                                    if isinstance(candidate, str):
                                        final_output = candidate
                                    continue
                                if kind == "model_start":
                                    model_started = True
                                if kind == "tool_start":
                                    tool_side_effect_started = True
                                if kind == "model_delta":
                                    if not model_started:
                                        yield self._publish(AgentEvent(
                                            event_id=uuid4().hex,
                                            run_id=run.run_id,
                                            kind="model_start",
                                            payload={},
                                            sequence=sequence,
                                        ))
                                        sequence += 1
                                        model_started = True
                                    text = payload.get("text")
                                    if isinstance(text, str):
                                        output_parts.append(text)
                                yield self._publish(AgentEvent(
                                    event_id=uuid4().hex,
                                    run_id=run.run_id,
                                    kind=kind,
                                    payload=payload,
                                    sequence=sequence,
                                ))
                                sequence += 1
                    else:
                        raw_result = await self._invoke(state, context=context)
                        output = self._output_from_result(raw_result)
                        history_captured = self._capture_history(raw_result)
                        if output:
                            yield self._publish(AgentEvent(
                                event_id=uuid4().hex,
                                run_id=run.run_id,
                                kind="model_start",
                                payload={},
                                sequence=sequence,
                            ))
                            sequence += 1
                            model_started = True
                            output_parts.append(output)
                            yield self._publish(AgentEvent(
                                event_id=uuid4().hex,
                                run_id=run.run_id,
                                kind="model_delta",
                                payload={"text": output},
                                sequence=sequence,
                            ))
                            sequence += 1
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if tool_side_effect_started or not self._retry_policy.should_retry(exc, attempt):
                        raise
                    yield self._publish(AgentEvent(
                        event_id=uuid4().hex,
                        run_id=run.run_id,
                        kind="retry",
                        payload={
                            "attempt": attempt,
                            "next_attempt": attempt + 1,
                            "error_code": self._error_code(exc),
                        },
                        sequence=sequence,
                    ))
                    sequence += 1
                    attempt += 1
                    output_parts.clear()
                    final_output = ""
                    model_started = False
                    await asyncio.sleep(self._retry_policy.delay_for(attempt - 1))
        except asyncio.CancelledError:
            if self._abort_requested:
                yield self._publish(AgentEvent(
                    event_id=uuid4().hex,
                    run_id=run.run_id,
                    kind="run_end",
                    payload={"status": RunStatus.ABORTED.value, "output": ""},
                    sequence=sequence,
                ))
                return
            raise
        except Exception as exc:  # noqa: BLE001 - stream errors become observable events
            yield self._publish(AgentEvent(
                event_id=uuid4().hex,
                run_id=run.run_id,
                kind="error",
                payload={"message": str(exc), "error_code": self._error_code(exc)},
                sequence=sequence,
            ))
            return
        else:
            output = "".join(output_parts) or final_output
            if output and not history_captured:
                self._message_history = [*state["messages"], output]
            yield self._publish(AgentEvent(
                event_id=uuid4().hex,
                run_id=run.run_id,
                kind="run_end",
                payload={"status": RunStatus.COMPLETED.value, "output": output},
                sequence=sequence,
            ))
        finally:
            if self._active_task is asyncio.current_task():
                self._active_task = None
            self._abort_requested = False

    def _capture_history(self, result: Any) -> bool:
        """Keep the complete message snapshot returned by the graph."""
        if isinstance(result, Mapping):
            messages = result.get("messages")
            if isinstance(messages, (list, tuple)):
                self._message_history = list(messages)
                return True
        return False

    def _capture_event_history(self, event: Any) -> bool:
        """Capture final graph state, excluding nested model/tool callbacks."""
        if not isinstance(event, Mapping) or event.get("event") != "on_chain_end":
            return False
        if event.get("parent_ids"):
            return False
        data = event.get("data")
        if isinstance(data, Mapping):
            return self._capture_history(data.get("output"))
        return False

    def _native_stream(self, state: dict[str, object], *, context: Mapping[str, Any] | None = None) -> Any:
        stream = getattr(self._agent, "astream", None)
        if not callable(stream):
            return None
        kwargs: dict[str, Any] = {}
        if self._accepts_keyword(stream, "stream_mode"):
            kwargs["stream_mode"] = "messages"
        if context is not None and self._accepts_keyword(stream, "context"):
            kwargs["context"] = context
        return stream(state, **kwargs)

    def _native_event_stream(self, state: dict[str, object], *, context: Mapping[str, Any] | None = None) -> Any:
        stream = getattr(self._agent, "astream_events", None)
        if not callable(stream):
            return None
        kwargs: dict[str, Any] = {}
        if self._accepts_keyword(stream, "version"):
            kwargs["version"] = "v2"
        if context is not None and self._accepts_keyword(stream, "context"):
            kwargs["context"] = context
        return stream(state, **kwargs)

    @classmethod
    def _normalize_chunk(cls, chunk: Any) -> list[tuple[str, dict[str, Any]]]:
        """Convert common LangGraph stream shapes into event kind/payload pairs."""
        if isinstance(chunk, tuple) and len(chunk) == 2:
            if isinstance(chunk[0], str) and chunk[0] in {"messages", "updates", "values", "custom"}:
                return cls._normalize_chunk(chunk[1])
            message, metadata = chunk
            node = cls._value(metadata, "langgraph_node") or cls._value(metadata, "node") or ""
            return cls._message_events(message, str(node))

        if isinstance(chunk, Mapping):
            explicit_kind = chunk.get("kind")
            if isinstance(explicit_kind, str):
                raw_payload = chunk.get("payload", {})
                payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {"value": raw_payload}
                return [(explicit_kind, payload)]

            chunk_type = chunk.get("type")
            if isinstance(chunk_type, str) and "data" in chunk:
                return cls._normalize_chunk(chunk["data"])
            if set(chunk) == {"data"}:
                return cls._normalize_chunk(chunk["data"])

            events: list[tuple[str, dict[str, Any]]] = []
            for node, value in chunk.items():
                if node in {"metadata", "ns", "type", "data", "checkpoint"}:
                    continue
                if isinstance(node, str):
                    events.extend(cls._node_events(node, value))
            if events:
                return events
            if "messages" in chunk:
                return cls._message_events(chunk["messages"], "")

        return cls._message_events(chunk, "")

    @classmethod
    def _normalize_callback_event(cls, chunk: Any) -> list[tuple[str, dict[str, Any]]]:
        if not isinstance(chunk, Mapping):
            return []
        event_name = chunk.get("event")
        if not isinstance(event_name, str):
            return cls._normalize_chunk(chunk)
        metadata = chunk.get('metadata')
        if (event_name in {'on_chat_model_start', 'on_chat_model_stream', 'on_chat_model_end'}
                and isinstance(metadata, Mapping) and metadata.get('lc_source') == 'summarization'):
            return []
        data = chunk.get("data")
        data_mapping = data if isinstance(data, Mapping) else {}
        name = str(chunk.get("name") or "")

        if event_name == "on_chain_start":
            return [("run_start", {})]
        if event_name == "on_chat_model_start":
            return [("model_start", {"name": name})]
        if event_name == "on_chat_model_stream":
            value = data_mapping.get("chunk", data_mapping.get("output", data))
            content = message_content(value)
            return [("model_delta", {"text": content})] if content else []
        if event_name == "on_tool_start":
            payload: dict[str, Any] = {"name": name}
            if "input" in data_mapping:
                payload["input"] = cls._json_safe(data_mapping["input"])
            call_id = chunk.get("run_id") or data_mapping.get("tool_call_id")
            if call_id is not None:
                payload["tool_call_id"] = str(call_id)
            return [("tool_start", payload)]
        if event_name == "on_tool_end":
            output = data_mapping.get("output", data)
            payload = {"name": name, "content": message_content(output)}
            # Native callback run_id is shared by start/end, unlike provider call IDs
            # which are not consistently included in start callback data.
            call_id = chunk.get("run_id") or data_mapping.get("tool_call_id") or cls._value(output, "tool_call_id")
            if call_id is not None:
                payload["tool_call_id"] = str(call_id)
            if cls._value(output, "status") == "error":
                payload["is_error"] = True
            return [("tool_result", payload)]
        if event_name == "on_chain_end":
            if chunk.get("parent_ids"):
                return []
            output = data_mapping.get("output", data)
            return [("run_end", {"output": cls._output_value(output)})]
        if event_name.endswith("_error"):
            error = data_mapping.get("error", data)
            return [("error", {"message": message_content(error), "error_code": "runtime_error"})]
        return []

    @staticmethod
    def _output_value(value: Any) -> str:
        if isinstance(value, Mapping) and isinstance(value.get("messages"), (list, tuple)):
            return message_content(last_assistant_message(value["messages"]))
        return message_content(value)

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, Mapping):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        return str(value)

    @classmethod
    def _node_events(cls, node: str, value: Any) -> list[tuple[str, dict[str, Any]]]:
        normalized_node = node.lower()
        if isinstance(value, Mapping):
            if "messages" in value:
                return cls._message_events(value["messages"], normalized_node)
            if "message" in value:
                return cls._message_events(value["message"], normalized_node)
            if "content" in value or "tool_call_id" in value:
                return cls._message_events(value, normalized_node)
        return cls._message_events(value, normalized_node)

    @classmethod
    def _message_events(cls, messages: Any, node: str) -> list[tuple[str, dict[str, Any]]]:
        if isinstance(messages, (list, tuple)):
            values = messages
        else:
            values = (messages,)

        events: list[tuple[str, dict[str, Any]]] = []
        for message in values:
            if message is None:
                continue
            message_type = str(cls._value(message, "type") or "").lower()
            content = message_content(message)
            is_tool = node in {"tool", "tools", "tool_result"} or message_type in {"tool", "tool_result"}
            if is_tool:
                payload: dict[str, Any] = {"content": content}
                if cls._value(message, "status") == "error":
                    payload["is_error"] = True
                call_id = cls._value(message, "tool_call_id") or cls._value(message, "call_id")
                name = cls._value(message, "name")
                if call_id is not None:
                    payload["tool_call_id"] = str(call_id)
                if name is not None:
                    payload["name"] = str(name)
                events.append(("tool_result", payload))
                continue

            if content:
                events.append(("model_delta", {"text": content}))

            tool_calls = cls._value(message, "tool_calls")
            if isinstance(tool_calls, (list, tuple)):
                for raw_call in tool_calls:
                    tool_call = cls._tool_call(raw_call)
                    events.append(("tool_start", {"tool_call": tool_call.to_dict()}))
        return events

    @staticmethod
    def _value(value: Any, name: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(name)
        return getattr(value, name, None)

    @classmethod
    def _tool_call(cls, value: Any) -> ToolCall:
        call_id = cls._value(value, "id") or cls._value(value, "call_id") or uuid4().hex
        name = cls._value(value, "name") or "unknown"
        arguments = cls._value(value, "args")
        if arguments is None:
            arguments = cls._value(value, "arguments")
        if not isinstance(arguments, Mapping):
            arguments = {}
        return ToolCall(call_id=str(call_id), name=str(name), arguments=arguments)

    async def _invoke(self, state: dict[str, object], *, context: Mapping[str, Any] | None = None) -> Any:
        async_invoke = getattr(self._agent, "ainvoke", None)
        if callable(async_invoke):
            kwargs = {"context": context} if context is not None and self._accepts_keyword(async_invoke, "context") else {}
            result = async_invoke(state, **kwargs)
            return await result if inspect.isawaitable(result) else result

        sync_invoke = getattr(self._agent, "invoke", None)
        if callable(sync_invoke):
            kwargs = {"context": context} if context is not None and self._accepts_keyword(sync_invoke, "context") else {}
            return await asyncio.to_thread(sync_invoke, state, **kwargs)
        raise RuntimeError("Agent does not provide ainvoke or invoke")

    def _resolve_request(
        self,
        request: str | AgentRequest,
    ) -> tuple[str, str, ModelRef, str, str | None, Mapping[str, Any]]:
        if isinstance(request, AgentRequest):
            return (
                request.prompt,
                request.session_id or self._session_id,
                request.model or self._model,
                request.workspace or self._workspace,
                request.parent_run_id,
                request.metadata,
            )
        if isinstance(request, str):
            return request, self._session_id, self._model, self._workspace, None, {}
        raise TypeError("request must be a string or AgentRequest")

    @staticmethod
    def _run_context(run: AgentRun) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "session_id": run.session_id,
            "model": run.model.to_dict(),
            "workspace": run.workspace,
            "reasoning_level": run.model.thinking_level,
            "parent_run_id": run.parent_run_id,
            "metadata": dict(run.metadata),
        }

    @staticmethod
    def _accepts_keyword(function: Any, keyword: str) -> bool:
        try:
            parameters = inspect.signature(function).parameters.values()
        except (TypeError, ValueError):
            return True
        return any(parameter.kind == inspect.Parameter.VAR_KEYWORD or parameter.name == keyword for parameter in parameters)

    @staticmethod
    def _validate_control_message(message: str) -> None:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("control message must be a non-empty string")

    def _claim_active(self) -> None:
        current = asyncio.current_task()
        if self._active_task is not None and not self._active_task.done():
            raise RuntimeError("An agent run is already active")
        self._active_task = current
        self._abort_requested = False

    def _publish(self, event: AgentEvent) -> AgentEvent:
        if self._recorder is not None:
            self._recorder.record(event)
        return event

    @staticmethod
    def _error_code(error: BaseException) -> str:
        if isinstance(error, PiDeepAgentError):
            return error.code.value
        return "runtime_error"

    def _aborted_result(self, run: AgentRun, start: AgentEvent) -> RunResult:
        end = self._publish(AgentEvent(
            event_id=uuid4().hex,
            run_id=run.run_id,
            kind="run_end",
            payload={"status": RunStatus.ABORTED.value, "output": ""},
            sequence=1,
        ))
        return RunResult(run_id=run.run_id, status=RunStatus.ABORTED, events=(start, end))

    @staticmethod
    def _output_from_result(result: Any) -> str:
        messages = result.get("messages") if isinstance(result, Mapping) else None
        if not isinstance(messages, list):
            raise RuntimeError("Agent returned no message history")
        return message_content(last_assistant_message(messages))


def ask_with_history(agent: AgentInvoker, prompt: str, history: Iterable[Any]) -> tuple[list[Any], str]:
    """Compatibility adapter for callers of the pre-M0 helper shape."""
    service = ApplicationService(agent, history)
    answer = service.ask(prompt)
    return list(service.history), answer


__all__ = [
    "AgentInvoker",
    "AgentLoop",
    "AgentLoopRuntime",
    "ApplicationService",
    "RetryPolicy",
    "ask_with_history",
    "last_assistant_message",
    "message_content",
]
