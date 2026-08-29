from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from pi_deepagent.contracts import (
    AgentEvent,
    AgentRun,
    ModelRef,
    RunResult,
    RunStatus,
    SessionId,
    ToolCall,
    ValidationError,
)
from pi_deepagent.commands import CommandRegistry
from pi_deepagent.capabilities import Capability, CapabilityProvider
from pi_deepagent.context import ContextLoader
from pi_deepagent.errors import (
    CommandError,
    ErrorCode,
    ExtensionError,
    PiDeepAgentError,
    ProviderError,
    SessionError,
    ToolError,
)
from pi_deepagent.extensions import ExtensionManager
from pi_deepagent.providers import ProviderAdapter, ProviderRegistry
from pi_deepagent.runtime import AgentLoop, ApplicationService
from pi_deepagent.sessions import SessionStore
from pi_deepagent.testing import FakeModel, FakeTool, InMemorySessionStore
from pi_deepagent.tools import ToolAdapter


class DomainContractTests(TestCase):
    def test_run_result_round_trips_without_langchain_messages(self) -> None:
        model = ModelRef(provider="fake", model_id="small", thinking_level="low")
        run = AgentRun(
            run_id="run-1",
            session_id="session-1",
            model=model,
            workspace=Path("C:/workspace"),
            started_at=datetime(2026, 8, 29, 1, 2, 3, tzinfo=timezone.utc),
            metadata={"attempt": 1},
        )
        call = ToolCall(call_id="call-1", name="fake_tool", arguments={"path": "a.txt"})
        event = AgentEvent(
            event_id="event-1",
            run_id=run.run_id,
            kind="tool_start",
            payload={"tool_call": call.to_dict()},
            sequence=1,
        )
        result = RunResult(
            run_id=run.run_id,
            status=RunStatus.COMPLETED,
            output="done",
            events=(event,),
            metadata={"model": model.to_dict()},
        )

        encoded = result.to_json()
        decoded = RunResult.from_json(encoded)

        self.assertEqual(decoded, result)
        self.assertEqual(json.loads(encoded)["status"], "completed")
        self.assertEqual(run.to_dict()["workspace"], "C:/workspace")
        self.assertNotIn("langchain", encoded.lower())

    def test_contracts_copy_mapping_inputs_and_use_stable_dict_shapes(self) -> None:
        arguments = {"value": "before"}
        call = ToolCall(call_id="call-1", name="fake_tool", arguments=arguments)
        arguments["value"] = "after"

        self.assertEqual(call.arguments, {"value": "before"})
        self.assertEqual(
            call.to_dict(),
            {"call_id": "call-1", "name": "fake_tool", "arguments": {"value": "before"}},
        )

    def test_empty_identifiers_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ModelRef(provider="", model_id="small")
        with self.assertRaises(ValidationError):
            ToolCall(call_id="call-1", name="")
        with self.assertRaises(ValidationError):
            AgentEvent(event_id="", run_id="run-1", kind="run_start")

    def test_malformed_mapping_and_workspace_values_raise_validation_error(self) -> None:
        with self.assertRaises(ValidationError):
            AgentEvent(event_id="event-1", run_id="run-1", kind="run_start", payload=[])
        with self.assertRaises(ValidationError):
            AgentRun(run_id="run-1", session_id="session-1", model=ModelRef("fake", "small"), workspace=None)


class LayerBoundaryTests(TestCase):
    def test_application_service_owns_conversation_history(self) -> None:
        class StubAgent:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def invoke(self, state: dict[str, object]) -> dict[str, list[str]]:
                self.calls.append(state)
                messages = [*state["messages"], f"answer-{len(self.calls)}"]
                return {"messages": messages}

        agent = StubAgent()
        service = ApplicationService(agent)

        self.assertEqual(service.ask("first"), "answer-1")
        self.assertEqual(service.ask("second"), "answer-2")
        self.assertEqual(agent.calls[0]["messages"], ["first"])
        self.assertEqual(agent.calls[1]["messages"], ["first", "answer-1", "second"])
        self.assertEqual(service.history, ("first", "answer-1", "second", "answer-2"))

        service.reset()
        self.assertEqual(service.history, ())

    def test_public_layer_boundaries_are_protocols(self) -> None:
        for boundary in (
            AgentLoop,
            ProviderAdapter,
            ProviderRegistry,
            ToolAdapter,
            SessionStore,
            ContextLoader,
            ExtensionManager,
            CommandRegistry,
        ):
            self.assertTrue(getattr(boundary, "_is_protocol", False), boundary)


class CapabilityAndErrorTests(TestCase):
    def test_capability_names_are_stable_and_provider_boundary_exposes_support(self) -> None:
        self.assertEqual(Capability.READ.value, "read")
        self.assertEqual(Capability.TOOL_CALLING.value, "tool_calling")
        self.assertTrue(getattr(CapabilityProvider, "_is_protocol", False))

    def test_errors_have_stable_codes_and_human_readable_details(self) -> None:
        errors = (
            (ProviderError("upstream unavailable", retryable=True), ErrorCode.PROVIDER),
            (ToolError("tool rejected input"), ErrorCode.TOOL),
            (SessionError("session conflict"), ErrorCode.SESSION),
            (ExtensionError("extension failed"), ErrorCode.EXTENSION),
            (CommandError("unknown command"), ErrorCode.COMMAND),
        )

        for error, code in errors:
            self.assertIsInstance(error, PiDeepAgentError)
            self.assertEqual(error.code, code)
            self.assertIn(error.message, str(error))
            self.assertEqual(error.to_dict()["code"], code.value)

        self.assertTrue(errors[0][0].retryable)
        self.assertFalse(errors[1][0].retryable)


class ContractFixtureTests(TestCase):
    def test_fake_model_is_deterministic_and_records_requests(self) -> None:
        model = FakeModel(["first", "second"])

        self.assertEqual(model.invoke("request-1"), "first")
        self.assertEqual(model.invoke("request-2"), "second")
        self.assertEqual(model.requests, ["request-1", "request-2"])
        with self.assertRaises(AssertionError):
            model.invoke("unexpected")

    def test_fake_tool_records_typed_calls_and_returns_handler_value(self) -> None:
        tool = FakeTool("add", lambda arguments: arguments["left"] + arguments["right"])

        self.assertEqual(tool.invoke({"left": 2, "right": 3}), 5)
        self.assertEqual(tool.calls[0].name, "add")
        self.assertEqual(tool.calls[0].arguments, {"left": 2, "right": 3})

    def test_in_memory_store_is_append_only_and_detects_version_conflicts(self) -> None:
        store = InMemorySessionStore()
        store.create("session-1", {"name": "fixture"})

        self.assertEqual(store.append("session-1", {"kind": "user", "text": "hello"}), 1)
        self.assertEqual(store.append("session-1", {"kind": "assistant", "text": "hi"}, expected_version=1), 2)
        self.assertEqual(
            list(store.load("session-1")),
            [
                {"kind": "user", "text": "hello"},
                {"kind": "assistant", "text": "hi"},
            ],
        )
        with self.assertRaises(SessionError):
            store.append("session-1", {"kind": "user", "text": "stale"}, expected_version=1)
