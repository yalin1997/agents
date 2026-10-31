"""M0 public middleware/routing contracts with a real Deep Agents graph.

StateBackend is used only for this framework probe, not a sandbox substitute.
The Linux backend and end-to-end isolation evidence are separate M0 gates.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.middleware.filesystem import FilesystemPermission
from deepagents.middleware.summarization import SummarizationMiddleware, SummarizationToolMiddleware

from pi_deepagent.collaboration.checkpoint import SqliteCheckpointSaver
from pi_deepagent.collaboration.middleware import TeamToolBoundary, TeamToolAccessError
from pi_deepagent.tool_output import SafeFilesystemMiddleware
from pi_deepagent.runtime import AgentLoopRuntime
from pi_deepagent.contracts import ModelRef


class RecordingModel(FakeMessagesListChatModel):
    seen_tools: list[list[str]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self.seen_tools.append([getattr(t, "name", "") for t in tools])
        return self


class ObservedCalls(BaseCallbackHandler):
    def __init__(self):
        self.sources = []

    def on_chat_model_start(self, serialized, messages, *, metadata=None, **kwargs):
        self.sources.append((metadata or {}).get("lc_source", "normal"))


class SummaryModel(BaseChatModel):
    """Controlled provider, real framework: summary calls are distinguished by tools."""
    main_calls: int = 0
    summary_calls: int = 0

    @property
    def _llm_type(self):
        return "controlled-summary-contract"

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=list(tools), **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if not kwargs.get("tools"):
            self.summary_calls += 1
            message = AIMessage(content="INTERNAL-SUMMARY: prior context saved; do not repeat completed tools.")
        else:
            self.main_calls += 1
            if self.main_calls == 1:
                message = AIMessage(content="", tool_calls=[
                    {"name": "large_probe", "args": {}, "id": "large-once", "type": "tool_call"}])
            else:
                message = AIMessage(content="visible final answer")
        return ChatResult(generations=[ChatGeneration(message=message)])


class FrameworkBoundaryTests(IsolatedAsyncioTestCase):
    async def test_task_is_hidden_and_forged_delegation_is_refused_per_graph(self):
        model = RecordingModel(responses=[AIMessage(content="", tool_calls=[
            {"name": "task", "args": {"description": "bypass", "subagent_type": "general-purpose"},
             "id": "forged", "type": "tool_call"}])])
        graph = create_deep_agent(model=model, middleware=[TeamToolBoundary()])
        with self.assertRaisesRegex(TeamToolAccessError, "team_tool_not_allowed"):
            await graph.ainvoke({"messages": "test delegation"}, {"recursion_limit": 8})
        self.assertEqual(len(model.seen_tools), 1)
        self.assertNotIn("task", model.seen_tools[0])
        regular = RecordingModel(responses=[AIMessage(content="regular")])
        result = await create_deep_agent(model=regular).ainvoke({"messages": "single-agent unchanged"})
        self.assertEqual(result["messages"][-1].content, "regular")
        self.assertIn("task", regular.seen_tools[0])

    async def test_framework_files_are_readable_but_write_aliases_are_denied(self):
        for path in ("/.framework/history.txt", ".framework/history.txt", "//.framework/history.txt",
                     "/src/../.framework/history.txt", "/.framework\\history.txt"):
            with self.subTest(path=path), TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "history.txt").write_text("preserve-history", encoding="utf-8")
                backend = CompositeBackend(default=StateBackend(),
                    routes={"/.framework/": FilesystemBackend(root_dir=directory, virtual_mode=True)},
                    artifacts_root="/.framework")
                model = RecordingModel(responses=[AIMessage(content="", tool_calls=[
                    {"name": "write_file", "args": {"file_path": path, "content": "overwrite"},
                     "id": "write-private", "type": "tool_call"}])])
                graph = create_deep_agent(model=model, backend=backend, middleware=[TeamToolBoundary()])
                with self.assertRaises(TeamToolAccessError):
                    await graph.ainvoke({"messages": "attempt framework overwrite"}, {"recursion_limit": 8})
                self.assertEqual((root / "history.txt").read_text(encoding="utf-8"), "preserve-history")
                read = backend.read("/.framework/history.txt")
                self.assertIsNone(read.error)
                self.assertEqual(read.file_data["content"], "preserve-history")

    async def test_native_summary_offload_and_callbacks_keep_history_outside_candidate(self):
        calls = []
        def large_probe() -> str:
            """Produce one large result; native middleware persists and replaces its preview."""
            calls.append("once")
            return "large-result 中文\n" * 3000
        observed = ObservedCalls()
        model = SummaryModel(callbacks=[observed])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "private"
            private.mkdir()
            backend = CompositeBackend(default=StateBackend(), routes={
                "/.framework/": FilesystemBackend(root_dir=private, virtual_mode=True)}, artifacts_root="/.framework")
            summary = SummarizationMiddleware(model=model, backend=backend,
                trigger=("messages", 4), keep=("messages", 1), trim_tokens_to_summarize=500)
            middleware = [TeamToolBoundary(), SafeFilesystemMiddleware(backend=backend, tool_token_limit_before_evict=128),
                          summary, SummarizationToolMiddleware(summary)]
            with SqliteCheckpointSaver(root / "graph.sqlite", thread_id="member-one", initialize=True) as saver:
                graph = create_deep_agent(model=model, backend=backend, tools=[large_probe], checkpointer=saver,
                    permissions=[FilesystemPermission(operations=["write"], paths=["/.framework/**"], mode="deny")],
                    middleware=middleware)
                cfg = {"configurable": {"thread_id": "member-one"}}
                await graph.aupdate_state(cfg, {"messages": [
                    HumanMessage(content="old evidence ORIGINAL-HISTORY"), AIMessage(content="old answer"),
                    HumanMessage(content="older followup"), AIMessage(content="older response")]})
                loop = AgentLoopRuntime(graph.with_config(cfg), model=ModelRef("controlled", "summary"), workspace=root)
                events = [event async for event in loop.stream("inspect and report")]
                self.assertEqual(events[-1].payload["status"], "completed")
                self.assertEqual(events[-1].payload["output"], "visible final answer")
                self.assertNotIn("INTERNAL-SUMMARY", "".join(e.payload.get("text", "") for e in events))
                self.assertEqual(calls, ["once"])
                self.assertGreaterEqual(model.summary_calls, 1)
                self.assertEqual(len(observed.sources), model.main_calls + model.summary_calls)
                self.assertIn("summarization", observed.sources)
                histories = list((private / "conversation_history").glob("*.md"))
                self.assertGreaterEqual(len(histories), 1)
                self.assertTrue(any("ORIGINAL-HISTORY" in p.read_text(encoding="utf-8") for p in histories))
                output = private / "large_tool_results" / "large-once"
                self.assertEqual(output.read_text(encoding="utf-8"), "large-result 中文\n" * 3000)
                state = await graph.aget_state(cfg)
                self.assertFalse(any(".framework" in p for p in state.values.get("files", {})))
            # Recreate graph-side backend; private persisted history remains independently readable.
            recreated = CompositeBackend(default=StateBackend(), routes={
                "/.framework/": FilesystemBackend(root_dir=private, virtual_mode=True)}, artifacts_root="/.framework")
            read = recreated.read("/.framework/large_tool_results/large-once", limit=1)
            self.assertIsNone(read.error)
            self.assertIn("large-result 中文", read.file_data["content"])


class FrameworkSyncTests(TestCase):
    def test_sync_graph_hides_delegation_and_allows_ordinary_work_file(self):
        model = RecordingModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "write_file", "args": {
                "file_path": "/product.txt", "content": "product-result"}, "id": "write-product", "type": "tool_call"}]),
            AIMessage(content="product written"),
        ])
        graph = create_deep_agent(model=model, middleware=[TeamToolBoundary()])
        result = graph.invoke({"messages": "write ordinary product file"})
        self.assertEqual(result["files"]["/product.txt"]["content"], "product-result")
        self.assertEqual(result["messages"][-1].content, "product written")
        self.assertEqual(len(model.seen_tools), 2)
        self.assertTrue(all("task" not in tools for tools in model.seen_tools))
