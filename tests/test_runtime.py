from __future__ import annotations

import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pi_deepagent.contracts import AgentEvent, AgentRequest, ModelRef, RunStatus, ValidationError
from pi_deepagent.errors import ProviderError
from pi_deepagent.events import EventRecorder
from pi_deepagent.runtime import AgentLoopRuntime, RetryPolicy


class _Message:
    def __init__(self, content: str) -> None:
        self.type = "ai"
        self.content = content


class AsyncGraph:
    def __init__(self) -> None:
        self.ainvoke_calls: list[dict[str, object]] = []

    async def ainvoke(self, state: dict[str, object], **kwargs: object) -> dict[str, list[_Message]]:
        self.ainvoke_calls.append(state)
        return {"messages": [_Message("async answer")]}


class HistoryGraph:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def ainvoke(self, state: dict[str, object], **kwargs: object) -> dict[str, list[object]]:
        self.calls.append(state)
        return {"messages": [*state["messages"], _Message(f"answer-{len(self.calls)}")]}


class SyncGraph:
    def __init__(self) -> None:
        self.invoke_calls: list[dict[str, object]] = []

    def invoke(self, state: dict[str, object], **kwargs: object) -> dict[str, list[_Message]]:
        self.invoke_calls.append(state)
        return {"messages": [_Message("sync answer")]}


class _ToolMessage:
    def __init__(self, content: str, call_id: str) -> None:
        self.type = "tool"
        self.content = content
        self.tool_call_id = call_id


class StreamingGraph:
    def __init__(self) -> None:
        self.astream_calls: list[dict[str, object]] = []

    async def astream(self, state: dict[str, object], **kwargs: object):
        self.astream_calls.append(state)
        yield {"model": {"messages": [_Message("hel")]}}
        yield {"tools": {"messages": [_ToolMessage("tool done", "call-1")]}}
        yield {"model": {"messages": [_Message("lo")]}}


class FlakyProviderGraph:
    def __init__(self, *, side_effect_started: bool = False) -> None:
        self.calls = 0
        self.side_effect_started = side_effect_started

    async def ainvoke(self, state: dict[str, object], **kwargs: object) -> dict[str, list[_Message]]:
        self.calls += 1
        if self.calls == 1:
            raise ProviderError(
                "temporary provider outage",
                retryable=True,
                details={"side_effect_started": self.side_effect_started},
            )
        return {"messages": [_Message("recovered")]}


class ContextGraph:
    def __init__(self) -> None:
        self.contexts: list[object] = []

    async def ainvoke(self, state: dict[str, object], **kwargs: object) -> dict[str, list[_Message]]:
        self.contexts.append(kwargs.get("context"))
        return {"messages": [_Message("context aware")]} 


class BlockingStreamingGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.steering: list[str] = []

    async def astream(self, state: dict[str, object], **kwargs: object):
        self.started.set()
        await self.release.wait()
        yield {"model": {"messages": [_Message("finished")]}}

    def steer(self, message: str) -> None:
        self.steering.append(message)


class EventStreamingGraph:
    def __init__(self) -> None:
        self.astream_events_calls: list[dict[str, object]] = []

    async def astream_events(self, state: dict[str, object], **kwargs: object):
        self.astream_events_calls.append(state)
        yield {"event": "on_chain_start", "name": "LangGraph", "data": {"input": state}}
        yield {"event": "on_chat_model_start", "name": "model", "data": {"input": state}}
        yield {"event": "on_chat_model_stream", "name": "model", "data": {"chunk": _Message("hello")}}
        yield {"event": "on_tool_start", "name": "pi_read", "data": {"input": {"path": "a.txt"}}}
        yield {"event": "on_tool_end", "name": "pi_read", "data": {"output": "file content"}}
        yield {
            "event": "on_chain_end",
            "name": "LangGraph",
            "data": {"output": {"messages": [_Message("hello")]}},
        }


class FlakyEventStreamingGraph:
    def __init__(self) -> None:
        self.calls = 0

    async def astream_events(self, state: dict[str, object], **kwargs: object):
        self.calls += 1
        if self.calls == 1:
            raise ProviderError("temporary stream outage", retryable=True)
        yield {"event": "on_chat_model_start", "name": "model", "data": {}}
        yield {"event": "on_chat_model_stream", "name": "model", "data": {"chunk": _Message("recovered")}}
        yield {"event": "on_chain_end", "name": "LangGraph", "data": {"output": "recovered"}}


class SideEffectThenFailStreamingGraph:
    def __init__(self) -> None:
        self.calls = 0

    async def astream_events(self, state: dict[str, object], **kwargs: object):
        self.calls += 1
        yield {"event": "on_tool_start", "name": "pi_write", "data": {"input": {"path": "a.txt"}}}
        raise ProviderError("provider failed after tool", retryable=True)


class SteerableBlockingGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.steering: list[str] = []

    async def ainvoke(self, state: dict[str, object], **kwargs: object) -> dict[str, list[_Message]]:
        self.started.set()
        await self.release.wait()
        return {"messages": [_Message("finished")]}

    def steer(self, message: str) -> None:
        self.steering.append(message)


class AgentLoopRunTests(IsolatedAsyncioTestCase):
    async def test_run_prefers_async_agent_and_returns_domain_result(self) -> None:
        graph = AsyncGraph()
        loop = AgentLoopRuntime(
            graph,
            model=ModelRef("fake", "small"),
            workspace="C:/workspace",
            session_id="session-1",
        )

        result = await loop.run("hello")

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.output, "async answer")
        self.assertEqual([event.kind for event in result.events], ["run_start", "run_end"])
        self.assertEqual(graph.ainvoke_calls, [{"messages": ["hello"]}])

    async def test_run_falls_back_to_sync_invoke_for_legacy_agents(self) -> None:
        graph = SyncGraph()
        loop = AgentLoopRuntime(graph, model=ModelRef("fake", "small"), workspace="C:/workspace")

        result = await loop.run("hello")

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.output, "sync answer")
        self.assertEqual(graph.invoke_calls, [{"messages": ["hello"]}])

    async def test_run_preserves_graph_message_history_across_turns(self) -> None:
        graph = HistoryGraph()
        loop = AgentLoopRuntime(graph, model=ModelRef("fake", "small"), workspace="C:/workspace")

        await loop.run("first")
        await loop.run("second")

        self.assertEqual(graph.calls[1]["messages"][0], "first")
        self.assertEqual(graph.calls[1]["messages"][1].content, "answer-1")
        self.assertEqual(graph.calls[1]["messages"][2], "second")


class AgentLoopStreamTests(IsolatedAsyncioTestCase):
    async def test_stream_keeps_every_turn_in_fallback_history(self) -> None:
        graph = StreamingGraph()
        loop = AgentLoopRuntime(graph, model=ModelRef("fake", "small"), workspace="C:/workspace")
        for prompt in ("first", "second", "third"):
            events = [event async for event in loop.stream(prompt)]
            self.assertEqual(events[-1].payload["status"], "completed")
            self.assertEqual(events[-1].payload["output"], "hello")
        self.assertEqual(graph.astream_calls[2]["messages"], ["first", "hello", "second", "hello", "third"])
        self.assertEqual(loop.message_history, ("first", "hello", "second", "hello", "third", "hello"))

    async def test_stream_normalizes_model_and_tool_chunks_to_public_events(self) -> None:
        graph = StreamingGraph()
        loop = AgentLoopRuntime(graph, model=ModelRef("fake", "small"), workspace="C:/workspace")

        events = [event async for event in loop.stream("hello")]

        self.assertEqual(
            [event.kind for event in events],
            ["run_start", "model_start", "model_delta", "tool_result", "model_delta", "run_end"],
        )
        self.assertEqual(events[2].payload["text"], "hel")
        self.assertEqual(events[3].payload["tool_call_id"], "call-1")
        self.assertEqual(events[-1].payload["output"], "hello")
        self.assertEqual(graph.astream_calls, [{"messages": ["hello"]}])


class AgentLoopControlTests(IsolatedAsyncioTestCase):
    async def test_steer_injects_message_into_an_active_agent(self) -> None:
        graph = SteerableBlockingGraph()
        loop = AgentLoopRuntime(graph, model=ModelRef("fake", "small"), workspace="C:/workspace")
        task = asyncio.create_task(loop.run("initial"))
        await asyncio.wait_for(graph.started.wait(), timeout=3)

        loop.steer("focus on tests")
        graph.release.set()
        result = await task

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(graph.steering, ["focus on tests"])

    async def test_follow_up_is_queued_until_the_next_run(self) -> None:
        graph = AsyncGraph()
        loop = AgentLoopRuntime(graph, model=ModelRef("fake", "small"), workspace="C:/workspace")

        loop.follow_up("queued request")
        self.assertEqual(loop.pending_messages, ("queued request",))
        result = await loop.run()

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(graph.ainvoke_calls, [{"messages": ["queued request"]}])
        self.assertEqual(loop.pending_messages, ())

    async def test_abort_returns_aborted_result_and_can_recover_or_drop_queue(self) -> None:
        graph = SteerableBlockingGraph()
        loop = AgentLoopRuntime(graph, model=ModelRef("fake", "small"), workspace="C:/workspace")
        task = asyncio.create_task(loop.run("initial"))
        await asyncio.wait_for(graph.started.wait(), timeout=3)
        loop.follow_up("continue later")

        loop.abort()
        result = await task

        self.assertEqual(result.status, RunStatus.ABORTED)
        self.assertEqual(result.events[-1].payload["status"], "aborted")
        self.assertEqual(loop.pending_messages, ("continue later",))

        loop.abort(discard_queued=True)
        self.assertEqual(loop.pending_messages, ())

    async def test_concurrent_run_is_rejected_while_another_run_is_active(self) -> None:
        graph = SteerableBlockingGraph()
        loop = AgentLoopRuntime(graph, model=ModelRef("fake", "small"), workspace="C:/workspace")
        task = asyncio.create_task(loop.run("initial"))
        await asyncio.wait_for(graph.started.wait(), timeout=3)

        with self.assertRaises(RuntimeError):
            await loop.run("second")

        loop.abort()
        await task


class AgentLoopRetryTests(IsolatedAsyncioTestCase):
    async def test_persistent_failure_stops_at_exact_attempt_limit(self) -> None:
        class FailingGraph:
            def __init__(self):
                self.calls = 0

            async def ainvoke(self, state, **kwargs):
                self.calls += 1
                raise ProviderError("still unavailable", retryable=True)

        for limit in (1, 2, 3):
            with self.subTest(max_attempts=limit):
                graph = FailingGraph()
                loop = AgentLoopRuntime(graph, model=ModelRef("fake", "small"), workspace=".",
                                        retry_policy=RetryPolicy(max_attempts=limit))
                result = await loop.run("hello")
                self.assertEqual(graph.calls, limit)
                self.assertEqual(result.status, RunStatus.FAILED)
                self.assertEqual(result.error_code, "provider_error")
                self.assertEqual(result.error, "still unavailable")
                self.assertEqual([e.kind for e in result.events], ["run_start", *(["retry"] * (limit - 1)), "error"])
                self.assertEqual([e.sequence for e in result.events], list(range(limit + 1)))

    async def test_nonzero_backoff_is_exponential_and_used_by_runtime(self) -> None:
        policy = RetryPolicy(max_attempts=3, backoff_seconds=0.25)
        self.assertEqual([policy.delay_for(i) for i in (1, 2, 3)], [0.25, 0.5, 1.0])
        graph = FlakyProviderGraph()
        loop = AgentLoopRuntime(graph, model=ModelRef("fake", "small"), workspace=".", retry_policy=policy)
        with patch("pi_deepagent.runtime.asyncio.sleep", autospec=True) as sleep:
            result = await loop.run("hello")
        self.assertEqual(result.status, RunStatus.COMPLETED)
        sleep.assert_awaited_once_with(0.25)

    async def test_stream_persistent_failure_stops_at_exact_attempt_limit(self) -> None:
        class FailingGraph:
            def __init__(self):
                self.calls = 0

            async def astream_events(self, state, **kwargs):
                self.calls += 1
                raise ProviderError("still unavailable", retryable=True)
                yield  # this graph exposes an async generator even when it fails

        for limit in (1, 2, 3):
            with self.subTest(max_attempts=limit):
                graph = FailingGraph()
                loop = AgentLoopRuntime(graph, model=ModelRef("fake", "small"), workspace=".",
                                        retry_policy=RetryPolicy(max_attempts=limit, backoff_seconds=0.25))
                with patch("pi_deepagent.runtime.asyncio.sleep", autospec=True) as sleep:
                    events = [event async for event in loop.stream("hello")]
                self.assertEqual(graph.calls, limit)
                self.assertEqual([e.kind for e in events], ["run_start", *(["retry"] * (limit - 1)), "error"])
                self.assertEqual(events[-1].payload["error_code"], "provider_error")
                self.assertEqual([e.sequence for e in events], list(range(limit + 1)))
                self.assertEqual([call.args[0] for call in sleep.await_args_list], [0.25, 0.5][:limit - 1])

    async def test_retryable_provider_failure_is_bounded_and_evented(self) -> None:
        graph = FlakyProviderGraph()
        loop = AgentLoopRuntime(
            graph,
            model=ModelRef("fake", "small"),
            workspace="C:/workspace",
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0),
        )

        result = await loop.run("hello")

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.output, "recovered")
        self.assertEqual(graph.calls, 2)
        retry_events = [event for event in result.events if event.kind == "retry"]
        self.assertEqual(len(retry_events), 1)
        self.assertEqual(retry_events[0].payload["attempt"], 1)

    async def test_provider_failure_after_tool_side_effect_is_not_retried(self) -> None:
        graph = FlakyProviderGraph(side_effect_started=True)
        loop = AgentLoopRuntime(
            graph,
            model=ModelRef("fake", "small"),
            workspace="C:/workspace",
            retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=0),
        )

        result = await loop.run("hello")

        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.error_code, "provider_error")
        self.assertEqual(graph.calls, 1)


class AgentLoopContextTests(IsolatedAsyncioTestCase):
    async def test_structured_request_is_exposed_as_framework_neutral_run_context(self) -> None:
        graph = ContextGraph()
        default_model = ModelRef("fake", "default")
        loop = AgentLoopRuntime(graph, model=default_model, workspace="C:/default", session_id="default-session")
        request = AgentRequest(
            prompt="hello",
            session_id="session-2",
            model=ModelRef("fake", "reasoning", thinking_level="high"),
            workspace="C:/request",
            parent_run_id="parent-run",
            metadata={"source": "test"},
        )

        result = await loop.run(request)

        context = graph.contexts[0]
        self.assertEqual(
            context,
            {
                "run_id": result.run_id,
                "session_id": "session-2",
                "model": {"provider": "fake", "model_id": "reasoning", "thinking_level": "high"},
                "workspace": "C:/request",
                "reasoning_level": "high",
                "parent_run_id": "parent-run",
                "metadata": {"source": "test"},
            },
        )
        self.assertEqual(result.metadata, context)


class EventRecorderTests(IsolatedAsyncioTestCase):
    def test_invalid_serialized_events_are_rejected(self) -> None:
        for value, message in (("not-json", "valid JSON"), ("{}", "must be an array"),
                               ("[1]", "each event must be an object")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValidationError, message):
                    EventRecorder.from_json(value)
        with self.assertRaisesRegex(TypeError, "accepts AgentEvent"):
            EventRecorder().record({"kind": "run_start"})

    def test_record_many_preserves_unicode_payloads_and_event_order(self) -> None:
        events = (AgentEvent("start", "run", "run_start", sequence=0),
                  AgentEvent("end", "run", "run_end", payload={"output": "完成 ✅"}, sequence=1))
        recorder = EventRecorder()
        recorder.record_many(events)
        restored = EventRecorder.from_json(recorder.to_json())
        self.assertEqual(restored.replay(), events)
        self.assertEqual(restored.to_dict()[1]["payload"], {"output": "完成 ✅"})

    async def test_recorder_round_trips_and_replays_events_in_order(self) -> None:
        graph = AsyncGraph()
        recorder = EventRecorder()
        loop = AgentLoopRuntime(
            graph,
            model=ModelRef("fake", "small"),
            workspace="C:/workspace",
            recorder=recorder,
        )

        result = await loop.run("hello")
        replayed: list[str] = []
        restored = EventRecorder.from_json(recorder.to_json())
        restored.replay(lambda event: replayed.append(event.kind))

        self.assertEqual(tuple(recorder.events), result.events)
        self.assertEqual(replayed, ["run_start", "run_end"])
        self.assertEqual(restored.events, recorder.events)


class AgentLoopStreamingControlTests(IsolatedAsyncioTestCase):
    async def test_streaming_run_can_receive_steering_and_abort(self) -> None:
        graph = BlockingStreamingGraph()
        loop = AgentLoopRuntime(graph, model=ModelRef("fake", "small"), workspace="C:/workspace")
        events = []

        async def consume() -> None:
            async for event in loop.stream("initial"):
                events.append(event)

        task = asyncio.create_task(consume())
        await asyncio.wait_for(graph.started.wait(), timeout=3)

        loop.steer("change direction")
        loop.abort()
        await task

        self.assertEqual(graph.steering, ["change direction"])
        self.assertEqual(events[0].kind, "run_start")
        self.assertEqual(events[-1].kind, "run_end")
        self.assertEqual(events[-1].payload["status"], "aborted")


class AgentLoopEventStreamTests(IsolatedAsyncioTestCase):
    def test_native_summary_tokens_are_internal_but_regular_model_tokens_remain_visible(self):
        event = {'event':'on_chat_model_stream','metadata':{'lc_source':'summarization'},'data':{'chunk':{'content':'internal summary'}}}
        self.assertEqual(AgentLoopRuntime._normalize_callback_event(event),[])
        event['metadata'] = {}
        self.assertEqual(AgentLoopRuntime._normalize_callback_event(event),[('model_delta',{'text':'internal summary'})])

    async def test_stream_preserves_root_history_for_the_next_turn(self) -> None:
        class Graph:
            def __init__(self) -> None:
                self.calls = []
                self.snapshots = []

            async def astream_events(self, state, **kwargs):
                self.calls.append(state)
                messages = [*state["messages"], _Message("answer")]
                self.snapshots.append(messages)
                yield {
                    "event": "on_chain_end", "parent_ids": [],
                    "data": {"output": {"messages": messages}},
                }
                yield {
                    "event": "on_chain_end", "parent_ids": ["root"],
                    "data": {"output": {"messages": [_Message("nested")]}},
                }

        graph = Graph()
        loop = AgentLoopRuntime(graph, model=ModelRef("fake", "small"), workspace="C:/workspace")
        first_events = [event async for event in loop.stream("first")]
        self.assertEqual(first_events[-1].kind, "run_end")
        self.assertEqual(first_events[-1].payload["output"], "answer")
        self.assertEqual(loop.message_history, tuple(graph.snapshots[0]))
        graph.snapshots[0].clear()
        second_events = [event async for event in loop.stream("second")]

        self.assertEqual(second_events[-1].kind, "run_end")
        self.assertEqual(second_events[-1].payload["output"], "answer")
        self.assertEqual(graph.calls[1]["messages"][0], "first")
        self.assertEqual(graph.calls[1]["messages"][1].content, "answer")
        self.assertEqual(graph.calls[1]["messages"][2], "second")

    def test_stream_mode_tuple_is_not_confused_with_message_metadata_tuple(self) -> None:
        events = AgentLoopRuntime._normalize_chunk(
            ("messages", (_Message("token"), {"langgraph_node": "model"}))
        )

        self.assertEqual(events, [("model_delta", {"text": "token"})])

    async def test_astream_events_is_primary_and_normalizes_v2_callback_events(self) -> None:
        graph = EventStreamingGraph()
        loop = AgentLoopRuntime(graph, model=ModelRef("fake", "small"), workspace="C:/workspace")

        events = [event async for event in loop.stream("hello")]

        self.assertEqual(
            [event.kind for event in events],
            ["run_start", "model_start", "model_delta", "tool_start", "tool_result", "run_end"],
        )
        self.assertEqual(events[2].payload["text"], "hello")
        self.assertEqual(events[3].payload["name"], "pi_read")
        self.assertEqual(events[4].payload["content"], "file content")
        self.assertEqual(events[-1].payload["output"], "hello")
        self.assertEqual(graph.astream_events_calls, [{"messages": ["hello"]}])

    async def test_retryable_stream_failure_restarts_before_tool_side_effects(self) -> None:
        graph = FlakyEventStreamingGraph()
        loop = AgentLoopRuntime(
            graph,
            model=ModelRef("fake", "small"),
            workspace="C:/workspace",
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0),
        )

        events = [event async for event in loop.stream("hello")]

        self.assertEqual(graph.calls, 2)
        self.assertEqual([event.kind for event in events], ["run_start", "retry", "model_start", "model_delta", "run_end"])
        self.assertEqual(events[-1].payload["output"], "recovered")

    async def test_stream_failure_after_tool_start_is_not_retried(self) -> None:
        graph = SideEffectThenFailStreamingGraph()
        loop = AgentLoopRuntime(
            graph,
            model=ModelRef("fake", "small"),
            workspace="C:/workspace",
            retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=0),
        )

        events = [event async for event in loop.stream("hello")]

        self.assertEqual(graph.calls, 1)
        self.assertEqual(events[-1].kind, "error")
        self.assertEqual(events[-1].payload["error_code"], "provider_error")


class ToolCallbackIdentityTests(IsolatedAsyncioTestCase):
    async def test_native_callbacks_share_identity_and_preserve_tool_error(self):
        from langchain_core.messages import ToolMessage
        start = AgentLoopRuntime._normalize_callback_event({'event':'on_tool_start', 'run_id':'callback-1', 'name':'pi_read', 'data':{'input':{'path':'a'}}})
        end = AgentLoopRuntime._normalize_callback_event({'event':'on_tool_end', 'run_id':'callback-1', 'name':'pi_read', 'data':{'output':ToolMessage(content='denied', tool_call_id='provider-id', status='error')}})
        self.assertEqual(start[0][1]['tool_call_id'], end[0][1]['tool_call_id'])
        self.assertEqual(end[0][1]['content'], 'denied')
        self.assertTrue(end[0][1]['is_error'])
