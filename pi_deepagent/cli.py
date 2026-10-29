"""Command-line entry point for pi_deepagent."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from .agent import create_pi_deepagent
from .config import load_environment
from .errors import PiDeepAgentError
from .runtime import ApplicationService, ask_with_history, last_assistant_message, message_content


def _format_error(error: BaseException) -> str:
    """Keep typed error codes visible while retaining readable CLI output."""
    if isinstance(error, PiDeepAgentError):
        return f"[{error.code.value}] {error.message}"
    return str(error)


def _message_content(message: Any) -> str:
    """Extract printable text from a LangChain message or content value."""
    return message_content(message)


def _last_assistant_message(messages: Sequence[Any]) -> Any:
    return last_assistant_message(messages)


def _ask(agent: Any, prompt: str, history: list[Any]) -> tuple[list[Any], str]:
    return ask_with_history(agent, prompt, history)


def _build_parser() -> argparse.ArgumentParser:
    load_environment()
    parser = argparse.ArgumentParser(description="Run a Pi-inspired LangChain Deep Agent.")
    parser.add_argument("prompt", nargs="*", help="Prompt to send to the agent")
    parser.add_argument(
        "--workspace",
        default=os.environ.get("PI_DEEPAGENT_WORKSPACE", "."),
        help="Workspace root directory",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("PI_DEEPAGENT_MODEL", "google_genai:gemini-3.6-flash"),
        help="LangChain model id",
    )
    parser.add_argument(
        "--shell",
        action="store_true",
        help="Enable the unrestricted local execute tool",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one prompt and exit; without this flag, no prompt starts a REPL",
    )
    return parser


def _run_once(agent: Any, prompt: str) -> int:
    try:
        _, answer = _ask(agent, prompt, [])
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI should report provider/tool errors cleanly
        print(f"Error: {_format_error(exc)}", file=sys.stderr)
        return 1
    if answer:
        print(answer)
    return 0


def _run_repl(agent: Any) -> int:
    service = ApplicationService(agent)
    while True:
        try:
            prompt = input("pi_deepagent> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print("\nUse /exit or Ctrl-D to leave.")
            continue

        if not prompt:
            continue
        if prompt.lower() in {"/exit", "/quit", "exit", "quit"}:
            return 0

        try:
            answer = service.ask(prompt)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            continue
        except Exception as exc:  # noqa: BLE001 - keep the REPL alive after one failed turn
            print(f"Error: {_format_error(exc)}", file=sys.stderr)
            continue
        if answer:
            print(answer)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    prompt = " ".join(args.prompt).strip()

    try:
        agent = create_pi_deepagent(
            workspace=Path(args.workspace).resolve(),
            model=args.model,
            shell=args.shell,
        )
    except Exception as exc:  # noqa: BLE001 - present configuration/provider errors as CLI output
        print(f"Error creating agent: {_format_error(exc)}", file=sys.stderr)
        return 1

    if prompt or args.once:
        if not prompt:
            try:
                prompt = input("pi_deepagent> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
        return _run_once(agent, prompt)
    return _run_repl(agent)


if __name__ == "__main__":
    raise SystemExit(main())
