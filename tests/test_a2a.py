"""A2A 1.0 contracts exercised over real loopback HTTP and the official SDK."""

from __future__ import annotations

import asyncio
import json
import socket
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

try:
    import a2a
    import uvicorn
except ImportError:
    from unittest import SkipTest
    raise SkipTest("Install the optional A2A dependencies with uv sync --extra a2a")

import httpx
import uvicorn
from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus
from a2a.utils.errors import TaskNotFoundError
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from pi_deepagent.a2a.client import RemoteClient, task_result
from pi_deepagent.a2a import cli as a2a_cli
from pi_deepagent.a2a.orchestrator import DelegatingSession
from pi_deepagent.a2a.server import create_app
from pi_deepagent.a2a.worker import DeepAgentWorker, WorkerOutcome, conversation_messages


class ClarifyingWorker:
    def __init__(self):
        self.calls = []

    async def run(self, messages):
        self.calls.append(messages)
        if messages[-1].content == "fail":
            raise RuntimeError("private provider failure")
        if messages[-1].content == "reject":
            return WorkerOutcome(outcome="rejected", text="Cannot perform that request")
        if messages[-1].content == "answer":
            return WorkerOutcome(outcome="completed", text="Done", result="A verified result")
        return WorkerOutcome(outcome="input_required", text="Please provide the audience")


class BlockingWorker:
    def __init__(self):
        self.started = asyncio.Event()
        self.canceled = asyncio.Event()

    async def run(self, messages):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.canceled.set()
            raise


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@asynccontextmanager
async def running_server(worker, database, *, timeout=5):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    app = create_app(worker, database=database, base_url=url, timeout=timeout)
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Server failed to start")
                await asyncio.sleep(0.01)
        yield url
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


class OutcomeTests(TestCase):
    def test_cli_main_returns_status_for_client_failures(self):
        for failure, code, message in ((ImportError("missing SDK"), 1, "uv sync --extra a2a"),
                                       (ValueError("bad session"), 1, "ValueError: bad session"),
                                       (KeyboardInterrupt(), 130, "")):
            with self.subTest(failure=type(failure).__name__):
                with patch("pi_deepagent.a2a.cli.run_client", autospec=True, side_effect=failure) as run, \
                        patch("pi_deepagent.a2a.cli.logging.basicConfig", autospec=True):
                    with redirect_stderr(StringIO()) as stderr:
                        self.assertEqual(a2a_cli.main(["tasks"]), code)
                    run.assert_awaited_once()
                    self.assertEqual(run.await_args.args[0].command, "tasks")
                    self.assertIn(message, stderr.getvalue())

    def test_cli_validates_timeout_and_port_before_starting_server(self):
        for timeout in ("0", "-1"):
            with self.subTest(timeout=timeout), redirect_stderr(StringIO()) as stderr, \
                    patch("pi_deepagent.a2a.cli.logging.basicConfig", autospec=True):
                with self.assertRaises(SystemExit) as error:
                    a2a_cli.main(["send", "hello", "--timeout", timeout])
                self.assertEqual(error.exception.code, 2)
                self.assertIn("timeout must be positive", stderr.getvalue())
        for port in (0, 65536):
            with self.subTest(port=port), redirect_stderr(StringIO()) as stderr, \
                    patch("pi_deepagent.a2a.cli.logging.basicConfig", autospec=True):
                self.assertEqual(a2a_cli.main(["serve", "--port", str(port)]), 1)
                self.assertIn("port must be between 1 and 65535", stderr.getvalue())

    def test_cli_send_flags_and_required_task_id(self):
        args = a2a_cli.parser().parse_args(["send", "hello", "--task-id", "task-1",
                                          "--no-stream", "--return-immediately"])
        self.assertEqual(args.text, "hello")
        self.assertEqual(args.task_id, "task-1")
        self.assertTrue(args.no_stream)
        self.assertTrue(args.return_immediately)
        for command in ("status", "cancel"):
            with self.subTest(command=command), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as error:
                    a2a_cli.parser().parse_args([command])
                self.assertEqual(error.exception.code, 2)

    def test_completion_requires_actual_result(self):
        with self.assertRaises(ValidationError):
            WorkerOutcome(outcome="completed", text="done")

    def test_question_cannot_be_a_completed_artifact(self):
        with self.assertRaises(ValidationError):
            WorkerOutcome(outcome="input_required", text="question", result="result")

    def test_unknown_outcome_or_extra_fields_are_rejected(self):
        for value in ({"outcome": "done", "text": "x"},
                      {"outcome": "input_required", "text": "x", "extra": True}):
            with self.assertRaises(ValidationError):
                WorkerOutcome.model_validate(value)

    def test_snapshot_completion_requires_artifacts(self):
        with self.assertRaises(ValueError):
            task_result(Task(id="t", context_id="c", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)))

    def test_specialized_factory_options_preserve_core_defaults(self):
        from pi_deepagent.agent import create_pi_deepagent
        with TemporaryDirectory() as workspace:
            with patch("deepagents.create_deep_agent", autospec=True, return_value="graph") as factory:
                create_pi_deepagent(workspace, model="fake:model", include_pi_tools=False,
                                    system_prompt="remote prompt", response_format=WorkerOutcome)
                kwargs = factory.call_args.kwargs
                self.assertEqual(kwargs["tools"], [])
                self.assertEqual(kwargs["system_prompt"], "remote prompt")
                self.assertIs(kwargs["response_format"], WorkerOutcome)

    def test_history_deduplicates_current_question_and_message(self):
        question = Message(message_id="q", role=Role.ROLE_AGENT, parts=[Part(text="Audience?")])
        answer = Message(message_id="a", role=Role.ROLE_USER, parts=[Part(text="Students")])
        task = Task(id="t", context_id="c", history=[question, answer],
                    status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED, message=question))
        self.assertEqual([m.content for m in conversation_messages(task, answer)], ["Audience?", "Students"])


class ProtocolTests(IsolatedAsyncioTestCase):
    async def test_cli_chat_routes_status_cancel_and_reports_invalid_attach(self):
        worker = ClarifyingWorker()
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "dispatch_remote_agent", "args": {
                "request": "Write"}, "id": "dispatch"}]),
            AIMessage(content="Waiting"),
        ])
        async with running_server(worker, self.database) as url:
            path = Path(self.temporary.name) / "chat.json"
            args = a2a_cli.parser().parse_args(["chat", "--url", url, "--workspace", self.temporary.name,
                                               "--session-file", str(path)])
            args.model = model
            with patch("builtins.input", autospec=True,
                       side_effect=["", "/status", "/attach missing", "Write", "/cancel", "/exit"]):
                with redirect_stdout(StringIO()) as stdout, redirect_stderr(StringIO()) as stderr:
                    await a2a_cli.run_client(args)
            self.assertIn('"state": "idle"', stdout.getvalue())
            self.assertIn("Please provide the audience", stdout.getvalue())
            self.assertIn("TASK_STATE_CANCELED", stdout.getvalue())
            self.assertIn("ValueError: attach is only for an uncertain send", stderr.getvalue())
            self.assertEqual(len(worker.calls), 1)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(data["uncertain"])
            async with RemoteClient(url) as client:
                self.assertEqual((await client.get(data["task_id"])).status.state, TaskState.TASK_STATE_CANCELED)

    async def test_cli_card_send_status_tasks_and_cancel_over_http(self):
        async def command(*arguments):
            args = a2a_cli.parser().parse_args([*arguments, "--url", url])
            with redirect_stdout(StringIO()) as stdout, redirect_stderr(StringIO()):
                await a2a_cli.run_client(args)
            return json.loads(stdout.getvalue())

        async with running_server(ClarifyingWorker(), self.database) as url:
            card = await command("card")
            self.assertEqual(card["name"], "Pi Remote Assistant")
            task = await command("send", "Write", "--no-stream")
            self.assertEqual(task["state"], "TASK_STATE_INPUT_REQUIRED")
            status = await command("status", task["task_id"])
            self.assertEqual(status["id"], task["task_id"])
            tasks = await command("tasks")
            self.assertIn(task["task_id"], [item["id"] for item in tasks["tasks"]])
            canceled = await command("cancel", task["task_id"])
            self.assertEqual(canceled["task_id"], task["task_id"])
            self.assertEqual(canceled["state"], "TASK_STATE_CANCELED")
            completed = await command("send", "answer")
            self.assertEqual(completed["state"], "TASK_STATE_COMPLETED")
            self.assertEqual(completed["artifacts"][0]["text"], "A verified result")

    async def test_restored_session_reconciles_uncertain_status_and_cancels(self):
        async with running_server(ClarifyingWorker(), self.database) as url:
            async with RemoteClient(url) as client:
                task = await client.send("Write", message_id="accepted-before-timeout")
                path = Path(self.temporary.name) / "reconcile.json"
                factory = lambda *args, **kwargs: None
                session = DelegatingSession(client, workspace=self.temporary.name, model="unused",
                                            session_file=path, graph_factory=factory)
                session.data.update(task_id=task.id, context_id=task.context_id,
                                    message_id="accepted-before-timeout", uncertain=True)
                session.save()
                restored = DelegatingSession(client, workspace=self.temporary.name, model="unused",
                                             session_file=path, graph_factory=factory)
                status = await restored.status()
                self.assertEqual(status["state"], "TASK_STATE_INPUT_REQUIRED")
                self.assertFalse(restored.data["uncertain"])
                self.assertFalse(json.loads(path.read_text(encoding="utf-8"))["uncertain"])
                canceled = await restored.cancel()
                self.assertEqual(canceled["task_id"], task.id)
                self.assertEqual(canceled["state"], "TASK_STATE_CANCELED")

    async def test_session_rejects_mismatched_identity_and_invalid_recovery(self):
        client = RemoteClient("http://127.0.0.1:41241")
        path = Path(self.temporary.name) / "identity.json"
        factory = lambda *args, **kwargs: None
        for version, url in ((2, client.url), (1, "http://other-server:1234")):
            with self.subTest(version=version, url=url):
                path.write_text(json.dumps({"version": version, "url": url, "messages": []}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Session version/agent URL mismatch"):
                    DelegatingSession(client, workspace=self.temporary.name, model="unused",
                                      session_file=path, graph_factory=factory)
        session = DelegatingSession(client, workspace=self.temporary.name, model="unused",
                                    session_file=path.with_name("new.json"), graph_factory=factory)
        self.assertEqual(await session.status(), {"state": "idle", "message_id": ""})
        with self.assertRaisesRegex(ValueError, "No known task"):
            await session.cancel()
        with self.assertRaisesRegex(ValueError, "only for an uncertain send"):
            await session.attach("unknown")
        with self.assertRaisesRegex(ValueError, "must not be blank"):
            await session.ask(" ")

    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory()
        self.database = Path(self.temporary.name) / "tasks.sqlite"

    async def asyncTearDown(self):
        self.temporary.cleanup()

    async def test_discovery_streaming_multiturn_and_artifacts(self):
        worker = ClarifyingWorker()
        async with running_server(worker, self.database) as url:
            async with RemoteClient(url) as client:
                self.assertTrue(client.card.capabilities.streaming)
                first = await client.send("Write an invitation")
                self.assertEqual(first.status.state, TaskState.TASK_STATE_INPUT_REQUIRED)
                second = await client.send("Still unsure", task_id=first.id)
                self.assertEqual(second.status.state, TaskState.TASK_STATE_INPUT_REQUIRED)
                result = await client.send("answer", task_id=first.id)
                self.assertEqual(result.id, first.id)
                self.assertEqual(result.context_id, first.context_id)
                self.assertEqual(result.status.state, TaskState.TASK_STATE_COMPLETED)
                self.assertEqual(task_result(result)["artifacts"][0]["text"], "A verified result")
                self.assertEqual([m.content for m in worker.calls[-1]], [
                    "Write an invitation", "Please provide the audience", "Still unsure",
                    "Please provide the audience", "answer",
                ])
                with self.assertRaises(ValueError):
                    await client.send("again", task_id=first.id)

    async def test_nonstreaming_same_contract(self):
        async with running_server(ClarifyingWorker(), self.database) as url:
            async with RemoteClient(url, streaming=False) as client:
                result = await client.send("answer")
                self.assertEqual(result.status.state, TaskState.TASK_STATE_COMPLETED)

    async def test_waiting_task_survives_server_restart(self):
        async with running_server(ClarifyingWorker(), self.database) as url:
            async with RemoteClient(url) as client:
                task = await client.send("Write an invitation")
        worker = ClarifyingWorker()
        async with running_server(worker, self.database) as url:
            async with RemoteClient(url) as client:
                result = await client.send("answer", task_id=task.id)
                self.assertEqual(result.id, task.id)
                self.assertEqual(result.status.state, TaskState.TASK_STATE_COMPLETED)
                self.assertIn("Write an invitation", [m.content for m in worker.calls[0]])

    async def test_cancel_waiting_task(self):
        async with running_server(ClarifyingWorker(), self.database) as url:
            async with RemoteClient(url) as client:
                task = await client.send("Write")
                canceled = await client.cancel(task.id)
                self.assertEqual(canceled.status.state, TaskState.TASK_STATE_CANCELED)
                self.assertEqual((await client.get(task.id)).status.state, TaskState.TASK_STATE_CANCELED)

    async def test_cancel_active_task(self):
        worker = BlockingWorker()
        async with running_server(worker, self.database) as url:
            async with RemoteClient(url, streaming=False) as client:
                task = await client.send("Work", return_immediately=True)
                await asyncio.wait_for(worker.started.wait(), 3)
                result = await client.cancel(task.id)
                self.assertEqual(result.status.state, TaskState.TASK_STATE_CANCELED)
                await asyncio.wait_for(worker.canceled.wait(), 3)

    async def test_return_immediately_on_streaming_client_does_not_block(self):
        worker = BlockingWorker()
        async with running_server(worker, self.database) as url:
            async with RemoteClient(url) as client:
                task = await asyncio.wait_for(client.send("work", return_immediately=True), 3)
                self.assertIn(task.status.state, {TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING})
                await client.cancel(task.id)

    async def test_timeout_is_failed_without_private_details(self):
        async with running_server(BlockingWorker(), self.database, timeout=0.05) as url:
            async with RemoteClient(url) as client:
                task = await client.send("work")
                self.assertEqual(task.status.state, TaskState.TASK_STATE_FAILED)
                self.assertNotIn("TimeoutError", task_result(task)["message"])

    async def test_failure_and_rejection_are_not_success(self):
        async with running_server(ClarifyingWorker(), self.database) as url:
            async with RemoteClient(url) as client:
                failed = await client.send("fail")
                self.assertEqual(failed.status.state, TaskState.TASK_STATE_FAILED)
                self.assertNotIn("private", task_result(failed)["message"])
                rejected = await client.send("reject")
                self.assertEqual(rejected.status.state, TaskState.TASK_STATE_REJECTED)

    async def test_context_mismatch_and_unknown_task(self):
        async with running_server(ClarifyingWorker(), self.database) as url:
            async with RemoteClient(url) as client:
                task = await client.send("Write")
                with self.assertRaises(ValueError):
                    await client.send("answer", task_id=task.id, context_id="wrong")
                with self.assertRaisesRegex(TaskNotFoundError, "Task not found"):
                    await client.get("missing")

    async def test_separate_tasks_have_isolated_histories(self):
        worker = ClarifyingWorker()
        async with running_server(worker, self.database) as url:
            async with RemoteClient(url) as client:
                first, second = await asyncio.gather(client.send("First"), client.send("Second"))
                self.assertNotEqual(first.id, second.id)
                await client.send("answer", task_id=first.id)
                self.assertNotIn("Second", [m.content for m in worker.calls[-1]])

    async def test_standard_jsonrpc_errors_and_version(self):
        async with running_server(ClarifyingWorker(), self.database) as url:
            async with httpx.AsyncClient() as http:
                response = await http.post(url + "/a2a/jsonrpc", json={
                    "jsonrpc": "2.0", "id": "bad", "method": "Nonexistent", "params": {},
                }, headers={"A2A-Version": "1.0"})
                self.assertIn("error", response.json())
                response = await http.post(url + "/a2a/jsonrpc", content="not-json",
                                           headers={"content-type": "application/json", "A2A-Version": "1.0"})
                self.assertIn("error", response.json())

    async def test_real_deepagent_graph_over_http_with_structured_tool_output(self):
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "WorkerOutcome", "args": {
                "outcome": "input_required", "text": "What audience?"}, "id": "q"}]),
            AIMessage(content="", tool_calls=[{"name": "WorkerOutcome", "args": {
                "outcome": "completed", "text": "Done", "result": "Invitation for students"}, "id": "r"}]),
        ])
        worker = DeepAgentWorker(self.temporary.name, model)
        async with running_server(worker, self.database) as url:
            async with RemoteClient(url) as client:
                task = await client.send("Write an invitation")
                self.assertEqual(task.status.state, TaskState.TASK_STATE_INPUT_REQUIRED)
                task = await client.send("Students", task_id=task.id)
                self.assertEqual(task_result(task)["artifacts"][0]["text"], "Invitation for students")

    async def test_orchestrator_delegates_then_routes_answer_without_llm_guess(self):
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "dispatch_remote_agent", "args": {
                "request": "Write an invitation"}, "id": "dispatch"}]),
            AIMessage(content="This misleading completion must not override INPUT_REQUIRED"),
        ])
        worker = ClarifyingWorker()
        async with running_server(worker, self.database) as url:
            async with RemoteClient(url) as client:
                path = Path(self.temporary.name) / "client.json"
                session = DelegatingSession(client, workspace=self.temporary.name, model=model, session_file=path)
                question = await session.ask("Write an invitation")
                self.assertIn("Please provide the audience", question)
                self.assertNotIn("misleading", question)
                task_id = session.data["task_id"]
                # A new local instance restores the pending task after a restart.
                restored = DelegatingSession(client, workspace=self.temporary.name, model=model, session_file=path)
                result = await restored.ask("answer")
                self.assertEqual(result, "A verified result")
                self.assertEqual(restored.data["task_id"], task_id)
                self.assertEqual(len(worker.calls), 2)

    async def test_uncertain_send_is_not_retried_and_can_be_attached(self):
        async with running_server(ClarifyingWorker(), self.database) as url:
            async with RemoteClient(url) as client:
                model = ToolModel(responses=[AIMessage(content="unused")])
                session = DelegatingSession(client, workspace=self.temporary.name, model=model,
                                            session_file=Path(self.temporary.name) / "uncertain.json")
                message_id = "lost-message"
                task = await client.send("Write", message_id=message_id)
                session.data.update(uncertain=True, message_id=message_id)
                session.save()
                with self.assertRaisesRegex(ValueError, "uncertain"):
                    await session.ask("do it again")
                await session.attach(task.id)
                self.assertFalse(session.data["uncertain"])
                self.assertEqual(session.data["task_id"], task.id)

    async def test_uncertain_send_cannot_attach_to_some_other_task(self):
        async with running_server(ClarifyingWorker(), self.database) as url:
            async with RemoteClient(url) as client:
                task = await client.send("Write", message_id="other-message")
                model = ToolModel(responses=[AIMessage(content="unused")])
                session = DelegatingSession(client, workspace=self.temporary.name, model=model,
                                            session_file=Path(self.temporary.name) / "uncertain.json")
                session.data.update(uncertain=True, message_id="lost-message")
                with self.assertRaisesRegex(ValueError, "history"):
                    await session.attach(task.id)
                self.assertTrue(session.data["uncertain"])

    async def test_startup_marks_abandoned_active_work_failed(self):
        from a2a.server.context import ServerCallContext
        from a2a.server.tasks.database_task_store import DatabaseTaskStore
        from sqlalchemy.ext.asyncio import create_async_engine
        engine = create_async_engine("sqlite+aiosqlite:///" + self.database.as_posix())
        store = DatabaseTaskStore(engine)
        await store.save(Task(id="abandoned", context_id="old", status=TaskStatus(
            state=TaskState.TASK_STATE_WORKING)), ServerCallContext())
        await engine.dispose()
        async with running_server(ClarifyingWorker(), self.database) as url:
            async with RemoteClient(url) as client:
                task = await client.get("abandoned")
                self.assertEqual(task.status.state, TaskState.TASK_STATE_FAILED)
                self.assertIn("restarted", task_result(task)["message"])

    async def test_card_origin_redirect_is_rejected(self):
        from pi_deepagent.a2a.server import make_card
        from google.protobuf.json_format import MessageToDict
        card = make_card("http://other-server:1234")
        def handler(request):
            return httpx.Response(200, json=MessageToDict(card))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with self.assertRaisesRegex(ValueError, "different origin"):
                async with RemoteClient("http://127.0.0.1:41241", http=http):
                    pass

    async def test_missing_structured_output_fails_instead_of_completing(self):
        class Graph:
            async def ainvoke(self, state):
                return {"messages": [AIMessage(content="Please provide more information") ]}
        worker = DeepAgentWorker(self.temporary.name, "unused", graph=Graph())
        async with running_server(worker, self.database) as url:
            async with RemoteClient(url) as client:
                task = await client.send("work")
                self.assertEqual(task.status.state, TaskState.TASK_STATE_FAILED)


if __name__ == "__main__":
    import unittest
    unittest.main()
