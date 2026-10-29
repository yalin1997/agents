from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from langchain_core.messages import AIMessage

from pi_deepagent import cli
from pi_deepagent.errors import ProviderError


class StubAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def invoke(self, state: dict[str, object]) -> dict[str, list[AIMessage]]:
        self.calls.append(state)
        return {"messages": [*state["messages"], AIMessage(content="stub answer")]}


class CliTests(TestCase):
    def test_error_formatter_preserves_stable_code_and_message(self) -> None:
        self.assertEqual(cli._format_error(ProviderError("upstream unavailable")), "[provider_error] upstream unavailable")

    def test_message_content_handles_multimodal_text_blocks(self) -> None:
        message = AIMessage(content=[{"type": "text", "text": "hello"}, {"type": "text", "text": " world"}])
        self.assertEqual(cli._message_content(message), "hello world")

    def test_repl_keeps_history_and_exits_with_command(self) -> None:
        agent = StubAgent()
        with patch("builtins.input", side_effect=["first", "second", "/exit"]), patch("builtins.print"):
            result = cli._run_repl(agent)

        self.assertEqual(result, 0)
        self.assertEqual(len(agent.calls), 2)
        self.assertEqual(agent.calls[0]["messages"], ["first"])
        self.assertEqual(agent.calls[1]["messages"][0], "first")
        self.assertEqual(agent.calls[1]["messages"][1].content, "stub answer")
        self.assertEqual(agent.calls[1]["messages"][2], "second")

    def test_once_mode_sends_prompt_to_created_agent(self) -> None:
        agent = StubAgent()
        with patch("pi_deepagent.cli.create_pi_deepagent", return_value=agent), patch("builtins.print") as print_mock:
            result = cli.main(["--once", "list", "files"])

        self.assertEqual(result, 0)
        self.assertEqual(agent.calls[0]["messages"], ["list files"])
        print_mock.assert_called_once_with("stub answer")
