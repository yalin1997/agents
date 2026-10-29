from __future__ import annotations

from pathlib import Path
import os
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import deepagents
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from pi_deepagent import AgentEvent, ModelRef, RunResult, SessionId
from pi_deepagent.agent import create_pi_deepagent


class ToolCapableFakeChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: object, **kwargs: object) -> ToolCapableFakeChatModel:
        return self


class AgentFactoryTests(TestCase):
    def test_loopback_openai_endpoint_uses_chat_completions(self) -> None:
        for endpoint_key in ("OPENAI_BASE_URL", "OPENAI_API_BASE"):
            for endpoint in ("http://127.0.0.1:8080/v1", "http://localhost:8080/v1", "http://[::1]:8080/v1"):
                with self.subTest(key=endpoint_key, endpoint=endpoint), TemporaryDirectory() as root:
                    configured = ToolCapableFakeChatModel(responses=[AIMessage(content='ok')], profile={'max_input_tokens':13312})
                    with patch.dict(os.environ, {endpoint_key: endpoint}, clear=True), \
                         patch("pi_deepagent.local_model.make_local_model", autospec=True, return_value=configured) as initialize, \
                         patch.object(deepagents, "create_deep_agent", autospec=True) as create:
                        create_pi_deepagent(root, model="openai:qwen3.8-27b")
                    initialize.assert_called_once_with("qwen3.8-27b", endpoint)
                    self.assertIs(create.call_args.kwargs["model"], configured)
                    from deepagents.middleware.summarization import SummarizationMiddleware
                    self.assertTrue(any(isinstance(m, SummarizationMiddleware) for m in create.call_args.kwargs['middleware']))

    def test_cloud_and_preconfigured_models_keep_their_api_configuration(self) -> None:
        configured = ToolCapableFakeChatModel(responses=[AIMessage(content="ok")])
        cases = [
            ({}, "openai:gpt-5.5"),
            ({"OPENAI_BASE_URL": "https://api.openai.com/v1"}, "openai:gpt-5.5"),
            ({"OPENAI_BASE_URL": "http://localhost.example/v1"}, "openai:custom"),
            ({"OPENAI_BASE_URL": "http://192.0.2.1/v1"}, "openai:custom"),
            ({"OPENAI_BASE_URL": "http://[invalid/v1"}, "openai:custom"),
            ({"OPENAI_BASE_URL": "http://127.0.0.1:8080/v1", "OPENAI_API_BASE": "https://api.openai.com/v1"}, "openai:gpt-5.5"),
            ({"OPENAI_BASE_URL": "http://127.0.0.1:8080/v1"}, "google_genai:gemini-3.6-flash"),
            ({"OPENAI_BASE_URL": "http://127.0.0.1:8080/v1"}, configured),
        ]
        for environment, model in cases:
            with self.subTest(environment=environment, model=type(model).__name__), TemporaryDirectory() as root:
                with patch.dict(os.environ, environment, clear=True), \
                     patch("pi_deepagent.local_model.make_local_model", autospec=True) as initialize, \
                     patch.object(deepagents, "create_deep_agent", autospec=True) as create:
                    create_pi_deepagent(root, model=model)
                initialize.assert_not_called()
                self.assertIs(create.call_args.kwargs["model"], model)

    def test_core_contracts_are_available_from_package_root(self) -> None:
        self.assertEqual(ModelRef("fake", "small").qualified_name, "fake/small")
        self.assertEqual(AgentEvent("event-1", "run-1", "run_start").kind, "run_start")
        self.assertEqual(RunResult("run-1", "completed").status.value, "completed")
        self.assertEqual(SessionId("session-1"), "session-1")

    def test_factory_graph_can_execute_a_workspace_bound_tool(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            agent = create_pi_deepagent(
                temporary_directory,
                model=ToolCapableFakeChatModel(
                    responses=[
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "pi_write",
                                    "args": {"path": "generated.txt", "content": "created by agent"},
                                    "id": "call-write",
                                    "type": "tool_call",
                                }
                            ],
                        ),
                        AIMessage(content="done"),
                    ]
                ),
            )

            result = agent.invoke({"messages": "create the file"})

            self.assertEqual((Path(temporary_directory) / "generated.txt").read_text(encoding="utf-8"), "created by agent")

        self.assertEqual(result["messages"][-1].content, "done")

    def test_factory_graph_completes_with_a_preconfigured_chat_model(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            agent = create_pi_deepagent(
                temporary_directory,
                model=ToolCapableFakeChatModel(responses=[AIMessage(content="ok")]),
            )

            result = agent.invoke({"messages": "hello"})

        self.assertEqual(result["messages"][-1].content, "ok")

    def test_factory_passes_workspace_bound_tools_to_deepagents(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with patch("deepagents.create_deep_agent", autospec=True, return_value="agent") as create_agent:
                result = create_pi_deepagent(root, model="test:model")

            self.assertEqual(result, "agent")
            kwargs = create_agent.call_args.kwargs
            self.assertEqual(kwargs["backend"].cwd, root.resolve())
            self.assertEqual(
                [tool.__name__ for tool in kwargs["tools"][:4]],
                ["pi_read", "pi_write", "pi_edit", "pi_bash"],
            )

            kwargs["tools"][1]("created.txt", "from bound tool")
            self.assertEqual((root / "created.txt").read_text(encoding="utf-8"), "from bound tool")

    def test_factory_preserves_extra_tools_and_subagents_compatibility(self) -> None:
        extra_tool = object()
        subagents = [{"name": "reviewer", "description": "reviews", "system_prompt": "review"}]
        with TemporaryDirectory() as temporary_directory:
            with patch("deepagents.create_deep_agent", autospec=True, return_value="agent") as create_agent:
                create_pi_deepagent(
                    temporary_directory,
                    model="test:model",
                    extra_tools=[extra_tool],
                    subagents=subagents,
                )

        kwargs = create_agent.call_args.kwargs
        self.assertEqual([tool.__name__ for tool in kwargs["tools"][:4]], ["pi_read", "pi_write", "pi_edit", "pi_bash"])
        self.assertIs(kwargs["tools"][4], extra_tool)
        self.assertIs(kwargs["subagents"], subagents)

    def test_shell_backend_has_a_working_environment_and_custom_timeout(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            with patch("deepagents.create_deep_agent", autospec=True, return_value="agent") as create_agent:
                create_pi_deepagent(
                    temporary_directory,
                    model="test:model",
                    shell=True,
                    shell_timeout=7,
                )
            backend = create_agent.call_args.kwargs["backend"]
            self.assertEqual(backend._default_timeout, 7)
            self.assertIn("PATH", backend._env)

        with TemporaryDirectory() as temporary_directory:
            with patch("deepagents.create_deep_agent", autospec=True, return_value="agent") as create_agent:
                create_pi_deepagent(
                    temporary_directory,
                    model="test:model",
                    shell=True,
                    shell_timeout=7,
                    inherit_env=False,
                )
            backend = create_agent.call_args.kwargs["backend"]
            self.assertEqual(backend._default_timeout, 7)
            self.assertEqual(backend._env, {})
