"""Real SQLite and graph tests for the public LangGraph saver contract."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.serde.types import ERROR

from pi_deepagent.collaboration.checkpoint import (
    CheckpointAccessError, CheckpointConflictError, CheckpointSchemaRequired,
    SqliteCheckpointSaver,
)


def config(thread="member-one", checkpoint_id=None, namespace=""):
    value = {"thread_id": thread, "checkpoint_ns": namespace}
    if checkpoint_id is not None:
        value["checkpoint_id"] = checkpoint_id
    return {"configurable": value}


def checkpoint(identifier="0001", value="first", version=1):
    result = empty_checkpoint()
    result.update(id=identifier, channel_values={"messages": [HumanMessage(content=value)]},
                  channel_versions={"messages": version}, versions_seen={"agent": {"messages": version}})
    return result


class CheckpointTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "checkpoints.sqlite"
        self.saver = SqliteCheckpointSaver(self.path, thread_id="member-one", initialize=True)
        self.addCleanup(self.saver.close)

    def test_full_checkpoint_parent_metadata_pending_writes_survive_reopen(self):
        first = checkpoint()
        first["channel_values"]["messages"].append(AIMessage(content="", tool_calls=[
            {"name": "probe", "args": {}, "id": "call-1", "type": "tool_call"}]))
        c1 = self.saver.put(config(), first, {"source": "input", "step": 0}, {"messages": 1})
        self.saver.put_writes(c1, [("messages", ToolMessage(content="known result", tool_call_id="call-1"))],
                              "task-1", "a")
        second = checkpoint("0002", "second", 2)
        c2 = self.saver.put(c1, second, {"source": "loop", "step": 1}, {"messages": 2})
        self.saver.close()
        with SqliteCheckpointSaver(self.path, thread_id="member-one") as reopened:
            latest = reopened.get_tuple(config())
            self.assertEqual(latest.checkpoint, second)
            self.assertEqual(latest.parent_config, c1)
            self.assertEqual(latest.metadata["step"], 1)
            historical = reopened.get_tuple(c1)
            self.assertEqual(historical.checkpoint, first)
            self.assertEqual(historical.pending_writes,
                             [("task-1", "messages", ToolMessage(content="known result", tool_call_id="call-1"))])
            self.assertEqual([x.config for x in reopened.list(config())], [c2, c1])
            self.assertEqual([x.config for x in reopened.list(config(), before=c2, limit=1)], [c1])
            self.assertEqual([x.config for x in reopened.list(None, filter={"source": "input"})], [c1])
            self.assertEqual(list(reopened.list(config(), limit=0)), [])
            self.assertIsNone(reopened.get_tuple(config(checkpoint_id="missing")))

    def test_thread_scope_rejects_foreign_reads_writes_history_and_delete(self):
        foreign = SqliteCheckpointSaver(self.path, thread_id="member-two")
        self.addCleanup(foreign.close)
        foreign.put(config("member-two"), checkpoint(value="private-two"), {}, {"messages": 1})
        operations = [
            lambda: self.saver.get_tuple(config("member-two")),
            lambda: self.saver.put(config("member-two"), checkpoint(), {}, {}),
            lambda: self.saver.put_writes(config("member-two", "0001"), [], "task"),
            lambda: list(self.saver.list(config("member-two"))),
            lambda: list(self.saver.list(None, before=config("member-two", "0002"))),
            lambda: self.saver.delete_thread("member-two"),
        ]
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(CheckpointAccessError):
                operation()
        self.assertEqual(list(self.saver.list(None)), [])
        self.assertEqual(foreign.get_tuple(config("member-two")).checkpoint["channel_values"]["messages"][0].content,
                         "private-two")

    def test_namespace_and_metadata_pagination_do_not_cross_boundaries(self):
        a = self.saver.put(config(namespace="a"), checkpoint("0001"), {"step": 0}, {"messages": 1})
        b = self.saver.put(config(namespace="b"), checkpoint("0002"), {"step": 1}, {"messages": 1})
        self.assertIsNone(self.saver.get_tuple(config()))
        self.assertEqual([x.config for x in self.saver.list(config(namespace="a"))], [a])
        self.assertEqual([x.config for x in self.saver.list(None)], [b, a])
        self.assertEqual([x.config for x in self.saver.list(None, filter={"step": 1})], [b])

    def test_duplicate_normal_writes_preserve_first_and_error_writes_update_in_task_path_order(self):
        saved = self.saver.put(config(), checkpoint(), {}, {"messages": 1})
        self.saver.put_writes(saved, [("answer", "late-path")], "task-z", "z")
        self.saver.put_writes(saved, [("answer", "original"), (ERROR, "first error")], "task-a", "a")
        self.saver.put_writes(saved, [("answer", "must not replace"), (ERROR, "final error")], "task-a", "a")
        self.assertEqual(self.saver.get_tuple(saved).pending_writes,
                         [("task-a", ERROR, "final error"), ("task-a", "answer", "original"),
                          ("task-z", "answer", "late-path")])

    def test_checkpoint_ids_are_immutable_and_retry_is_idempotent(self):
        original = checkpoint()
        saved = self.saver.put(config(), original, {"step": 0}, {"messages": 1})
        self.assertEqual(self.saver.put(config(), original, {"step": 0}, {"messages": 1}), saved)
        with self.assertRaises(CheckpointConflictError):
            self.saver.put(config(), checkpoint(value="overwrite"), {"step": 0}, {"messages": 1})
        with self.assertRaises(CheckpointConflictError):
            self.saver.put(config(), original, {"step": 99}, {"messages": 1})
        self.assertEqual(self.saver.get(saved), original)

    def test_partial_checkpoint_reuses_channel_blob_but_cannot_rewrite_version(self):
        first = self.saver.put(config(), checkpoint(), {}, {"messages": 1})
        partial = checkpoint("0002")
        partial["channel_values"] = {}
        second = self.saver.put(first, partial, {}, {})
        self.assertEqual(self.saver.get(second)["channel_values"],
                         {"messages": [HumanMessage(content="first")]})
        with self.assertRaises(CheckpointConflictError):
            self.saver.put(first, checkpoint("0003", value="replace same channel version"), {}, {"messages": 1})
        self.assertIsNone(self.saver.get_tuple(config(checkpoint_id="0003")))
        self.assertEqual([x.config for x in self.saver.list(second)], [second])

    def test_invalid_thread_binding_and_namespace_are_rejected(self):
        with self.assertRaises(ValueError):
            SqliteCheckpointSaver(self.path, thread_id="")
        with self.assertRaises(ValueError):
            self.saver.get_tuple(config(namespace=1))

    def test_delete_only_bound_thread_and_closed_saver_cannot_be_reused(self):
        self.saver.put(config(), checkpoint(), {}, {})
        self.saver.delete_thread("member-one")
        self.assertIsNone(self.saver.get_tuple(config()))
        self.saver.close()
        self.saver.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.saver.get_tuple(config())

    def test_schema_creation_requires_explicit_empty_database(self):
        missing = self.path.parent / "missing.sqlite"
        with self.assertRaises(CheckpointSchemaRequired):
            SqliteCheckpointSaver(missing, thread_id="one")
        self.assertFalse(missing.exists())
        existing = self.path.parent / "existing.sqlite"
        with closing(sqlite3.connect(existing)) as db, db:
            db.execute("CREATE TABLE tasks (id TEXT)")
            db.execute("INSERT INTO tasks VALUES ('preserve')")
        with self.assertRaises(CheckpointSchemaRequired):
            SqliteCheckpointSaver(existing, thread_id="one", initialize=True)
        with closing(sqlite3.connect(existing)) as db:
            self.assertEqual(db.execute("SELECT * FROM tasks").fetchall(), [("preserve",)])
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("tasks",)])

    def test_concurrent_connections_keep_all_writes(self):
        saved = self.saver.put(config(), checkpoint(), {}, {})
        def write(index):
            with SqliteCheckpointSaver(self.path, thread_id="member-one") as saver:
                saver.put_writes(saved, [("answer", index)], f"task-{index:02d}", f"{index:02d}")
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(write, range(12)))
        self.assertEqual([v for _, _, v in self.saver.get_tuple(saved).pending_writes], list(range(12)))

    def test_base_delta_history_walks_only_parent_chain_and_preserves_order(self):
        first = checkpoint()
        first["channel_values"] = {"delta": ["seed"]}
        first["channel_versions"] = {"delta": 1}
        c1 = self.saver.put(config(), first, {}, {})
        self.saver.put_writes(c1, [("delta", "B")], "task-b", "b")
        self.saver.put_writes(c1, [("delta", "A")], "task-a", "a")
        child = checkpoint("0002")
        child["channel_values"] = {}
        child["channel_versions"] = {"delta": 2}
        c2 = self.saver.put(c1, child, {}, {"delta": 2})
        self.saver.put(c1, {**child, "id": "0003"}, {}, {"delta": 2})  # unrelated branch
        self.assertEqual(self.saver.get_delta_channel_history(config=c2, channels=["delta"]),
                         {"delta": {"seed": ["seed"], "writes": [("task-a", "delta", "A"), ("task-b", "delta", "B")]}})
        self.assertEqual(self.saver.get_delta_channel_history(config=c2, channels=[]), {})


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class CheckpointGraphTests(IsolatedAsyncioTestCase):
    async def test_real_deepagent_uses_durable_tool_pair_and_next_turn_without_replay(self):
        from deepagents import create_deep_agent
        calls = []
        def probe() -> str:
            """Record one observable tool execution."""
            calls.append("executed")
            return "durable-result-中文"
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "probe", "args": {}, "id": "probe-1", "type": "tool_call"}]),
            AIMessage(content="first answer"), AIMessage(content="second answer"),
        ])
        with TemporaryDirectory() as directory:
            path = Path(directory) / "graph.sqlite"
            with SqliteCheckpointSaver(path, thread_id="member-one", initialize=True) as saver:
                graph = create_deep_agent(model=model, tools=[probe], checkpointer=saver)
                result = await graph.ainvoke({"messages": "first question"}, config())
                self.assertEqual(result["messages"][-1].content, "first answer")
            with SqliteCheckpointSaver(path, thread_id="member-one") as reopened:
                graph = create_deep_agent(model=model, tools=[probe], checkpointer=reopened)
                # Completed boundary only. Runtime must not resume pending operations after restart.
                self.assertEqual((await graph.aget_state(config())).next, ())
                result = await graph.ainvoke({"messages": "next question"}, config())
                tools = [m for m in result["messages"] if isinstance(m, ToolMessage)]
                self.assertEqual([(m.tool_call_id, m.content) for m in tools], [("probe-1", "durable-result-中文")])
                self.assertEqual(result["messages"][-1].content, "second answer")
                history = [s async for s in graph.aget_state_history(config())]
                self.assertGreaterEqual(len(history), 6)
                self.assertEqual(calls, ["executed"])

    async def test_async_full_contract_and_pending_state_after_cancellation(self):
        from langgraph.graph import StateGraph, START, END
        entered = asyncio.Event()
        async def wait_for_cancel(state):
            entered.set()
            await asyncio.Event().wait()
            return state
        with TemporaryDirectory() as directory:
            with SqliteCheckpointSaver(Path(directory) / "graph.sqlite", thread_id="member-one", initialize=True) as saver:
                builder = StateGraph(dict)
                builder.add_node("blocked", wait_for_cancel)
                builder.add_edge(START, "blocked")
                builder.add_edge("blocked", END)
                graph = builder.compile(checkpointer=saver)
                task = asyncio.create_task(graph.ainvoke({"input": "saved"}, config()))
                await asyncio.wait_for(entered.wait(), 5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                state = await graph.aget_state(config())
                self.assertEqual(state.next, ("blocked",))
                self.assertEqual(state.values, {"input": "saved"})
                history = [x async for x in saver.alist(config(), limit=1)]
                self.assertEqual(len(history), 1)
                self.assertIsNotNone(await saver.aget_tuple(config()))
                await saver.adelete_thread("member-one")
                self.assertIsNone(await saver.aget_tuple(config()))
