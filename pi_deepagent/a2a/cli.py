"""Serve, inspect and converse using the official A2A 1.0 client/server SDK."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from ..config import load_environment


def parser() -> argparse.ArgumentParser:
    load_environment()
    root = argparse.ArgumentParser(description="Pi A2A 1.0 (official SDK, one-hop delegation)")
    sub = root.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run a real DeepAgent A2A server on loopback")
    serve.add_argument("--port", type=int, default=41241)
    serve.add_argument("--workspace", default=".")
    serve.add_argument("--database", default=".pi-a2a/tasks.sqlite")
    serve.add_argument("--timeout", type=float, default=180)
    serve.add_argument("--model", default=os.getenv("PI_DEEPAGENT_MODEL", "google_genai:gemini-3.6-flash"))
    for name in ("card", "tasks", "send", "status", "cancel", "chat"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--url", default="http://127.0.0.1:41241")
        cmd.add_argument("--timeout", type=float, default=200)
        if name in {"status", "cancel"}:
            cmd.add_argument("task_id")
        if name == "send":
            cmd.add_argument("text")
            cmd.add_argument("--task-id", default="")
            cmd.add_argument("--no-stream", action="store_true")
            cmd.add_argument("--return-immediately", action="store_true")
        if name == "chat":
            cmd.add_argument("--workspace", default=".")
            cmd.add_argument("--session-file", default=".pi-a2a/client.json")
            cmd.add_argument("--model", default=os.getenv("PI_DEEPAGENT_MODEL", "google_genai:gemini-3.6-flash"))
    return root


async def run_client(args) -> None:
    from google.protobuf.json_format import MessageToDict
    from .client import RemoteClient, task_result

    async with RemoteClient(args.url, timeout=args.timeout,
                            streaming=not (getattr(args, "no_stream", False)
                                           or getattr(args, "return_immediately", False))) as remote:
        if args.command == "card":
            result = MessageToDict(remote.card)
        elif args.command == "tasks":
            result = await remote.list()
        elif args.command == "status":
            result = MessageToDict(await remote.get(args.task_id))
        elif args.command == "cancel":
            result = task_result(await remote.cancel(args.task_id))
        elif args.command == "send":
            def progress(task):
                print(f"Task: {task.id} (context: {task.context_id})", file=sys.stderr)
            result = task_result(await remote.send(
                args.text, task_id=args.task_id, on_task=progress,
                return_immediately=args.return_immediately,
            ))
        else:
            from .orchestrator import DelegatingSession
            session = DelegatingSession(remote, workspace=str(Path(args.workspace).resolve()),
                                        model=args.model, session_file=args.session_file)
            print("Connected. /status, /cancel, /attach TASK_ID, /exit. Pending answers resume the original task.")
            print(json.dumps(await session.status(), ensure_ascii=False))
            while True:
                try:
                    text = (await asyncio.to_thread(input, "pi-a2a> ")).strip()
                except EOFError:
                    return
                if text == "/exit":
                    return
                if not text:
                    continue
                try:
                    if text == "/status":
                        print(json.dumps(await session.status(), ensure_ascii=False, indent=2))
                    elif text == "/cancel":
                        print(json.dumps(await session.cancel(), ensure_ascii=False, indent=2))
                    elif text.startswith("/attach "):
                        print(json.dumps(await session.attach(text.split(maxsplit=1)[1]), ensure_ascii=False, indent=2))
                    else:
                        print(await session.ask(text))
                except Exception as exc:
                    print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    if args.timeout <= 0:
        parser().error("timeout must be positive")
    try:
        if args.command == "serve":
            import uvicorn
            from .server import create_app
            from .worker import DeepAgentWorker
            if not 1 <= args.port <= 65535:
                raise ValueError("port must be between 1 and 65535")
            worker = DeepAgentWorker(str(Path(args.workspace).resolve()), args.model)
            app = create_app(worker, database=args.database,
                             base_url=f"http://127.0.0.1:{args.port}", timeout=args.timeout)
            uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
        else:
            asyncio.run(run_client(args))
    except KeyboardInterrupt:
        return 130
    except ImportError as exc:
        print(f"A2A dependencies unavailable; run uv sync --extra a2a. ({exc})", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
