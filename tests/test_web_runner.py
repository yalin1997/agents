from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pi_deepagent.web.runner import make_runner_factory


class EventGraph:
    def __init__(self):
        self.inputs = []
    async def astream_events(self, state, **kwargs):
        self.inputs.append(state)
        yield {"event":"on_chat_model_stream", "data":{"chunk":{"content":"answer"}}}
        yield {"event":"on_chain_end", "data":{"output":{"messages":[{"type":"ai", "content":"answer"}]}}}


class RunnerTests(IsolatedAsyncioTestCase):
    async def test_local_large_tool_result_fits_context_and_tool_is_not_replayed(self):
        await self.large_tool_round_trip()

    async def test_local_server_context_rejection_reduces_model_request_without_replaying_tool(self):
        await self.large_tool_round_trip(force_overflow=True)

    async def large_tool_round_trip(self, force_overflow=False):
        from langchain_openai._compat import httpx
        from tempfile import TemporaryDirectory

        requests = []
        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            summary = not body.get('tools')
            if summary and not body.get('stream'):
                return httpx.Response(200,json={'id':'summary','object':'chat.completion','created':1,'model':'qwen-test','choices':[{'index':0,'message':{'role':'assistant','content':'The user requested large.txt. pi_read already executed; continue without repeating it.'},'finish_reason':'stop'}]},request=request)
            returned = [m for m in body['messages'] if m['role'] == 'tool']
            if summary:
                delta = {'role':'assistant','content':'The user requested large.txt. pi_read already executed; continue without repeating it.'}
            elif returned:
                if force_overflow and len(requests)==2:
                    return httpx.Response(400,json={'error':{'code':400,'type':'exceed_context_size_error','message':'request (18000 tokens) exceeds the available context size (16384 tokens)','n_prompt_tokens':18000,'n_ctx':16384}},request=request)
                # The old implementation sent the entire 50k-character tool result.
                if len(returned[0]['content']) > 10000:
                    return httpx.Response(400, json={'error':{'code':400,'type':'exceed_context_size_error','message':'request (32541 tokens) exceeds the available context size (16384 tokens)','n_prompt_tokens':32541,'n_ctx':16384}}, request=request)
                delta = {'role':'assistant','content':'large tool result handled'}
            else:
                delta = {'role':'assistant','tool_calls':[{'index':0,'id':'read-large','type':'function','function':{'name':'pi_read','arguments':'{"path":"large.txt"}'}}]}
            chunks = [{'id':'test','object':'chat.completion.chunk','created':1,'model':'qwen-test','choices':[{'index':0,'delta':delta,'finish_reason':None}]},
                      {'id':'test','object':'chat.completion.chunk','created':1,'model':'qwen-test','choices':[{'index':0,'delta':{},'finish_reason':'stop' if returned or summary else 'tool_calls'}]}]
            data = ''.join('data: '+json.dumps(chunk)+'\n\n' for chunk in chunks)+'data: [DONE]\n\n'
            return httpx.Response(200,content=data.encode(),headers={'content-type':'text/event-stream'},request=request)
        async def send(client, request, **kwargs): return respond(request)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'large.txt').write_text('large tool output 中文 '*10000,encoding='utf-8')
            with patch.dict(os.environ,{'OPENAI_API_BASE':'','OPENAI_BASE_URL':'http://127.0.0.1:9090/v1','OPENAI_API_KEY':'offline-only','PI_DEEPAGENT_CONTEXT_WINDOW':'16384'}), patch.object(httpx.AsyncClient,'send',send):
                history = [{'role':'user','text':'Earlier task context'}, {'role':'assistant','text':'prior fact '*160}] if force_overflow else []
                runner = make_runner_factory(root,'openai:qwen-test')(history,'read large.txt')
                events = [e async for e in runner.stream()]
            saved = list((root/'large_tool_results').glob('*'))
            self.assertTrue(saved)
            self.assertTrue(any(len(path.read_text(encoding='utf-8')) >= 50000 for path in saved if path.is_file()))
            if force_overflow:
                histories = list((root/'conversation_history').glob('*.md'))
                self.assertTrue(histories)
                self.assertTrue(any('prior fact' in p.read_text(encoding='utf-8') for p in histories))
        self.assertEqual([e.payload for e in events if e.kind=='error'],[])
        self.assertEqual(events[-1].payload['status'],'completed')
        self.assertEqual(events[-1].payload['output'],'large tool result handled')
        self.assertEqual([e.payload['name'] for e in events if e.kind=='tool_start'],['pi_read'])
        self.assertEqual(len(requests),4 if force_overflow else 2)
        if force_overflow:
            self.assertEqual(sum(not r.get('tools') for r in requests),1)
            self.assertIn('continue without repeating',json.dumps(requests[-1]))
        tool = next(m for m in requests[-1]['messages'] if m['role']=='tool')
        self.assertIn('large_tool_results',tool['content'])
        self.assertEqual(tool['tool_call_id'],'read-large')
        self.assertLessEqual(requests[-1].get('max_tokens',requests[-1].get('max_completion_tokens')),2048)

    async def test_runtime_error_retains_graph_stack_for_local_diagnostics(self):
        class BrokenGraph:
            async def astream_events(self, state, **kwargs):
                raise ValueError('invalid tool response')
                yield {}
        with patch('pi_deepagent.web.runner.create_pi_deepagent', return_value=BrokenGraph()):
            runner = make_runner_factory(Path.cwd(), 'fake:local')([], 'test')
            events = [event async for event in runner.stream()]
        self.assertEqual(events[-1].kind, 'error')
        self.assertEqual(events[-1].payload['message'], 'invalid tool response')
        self.assertIn('test_web_runner.py:', runner.error_traceback)
        self.assertNotIn('raise ValueError', runner.error_traceback)

    async def test_local_openai_tool_round_trip_uses_chat_completions_wire_format(self):
        # OpenAI SDK 3 uses httpx2; intercept the transport it actually uses.
        from langchain_openai._compat import httpx
        from tempfile import TemporaryDirectory

        requests = []
        def respond(request):
            body = json.loads(request.content)
            requests.append((request.url.path, body))
            if request.url.path != "/v1/chat/completions":
                return httpx.Response(400, json={"error": {"message": "item['content'] is empty", "type": "invalid_request_error", "code": 400}}, request=request)
            first = len(requests) == 1
            delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call-read", "type": "function", "function": {"name": "pi_read", "arguments": json.dumps({"path": "probe.txt"})}}]} if first else {"role": "assistant", "content": "已讀取 wire-probe-ok"}
            def chunk(delta, finish):
                return {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1, "model": "qwen-test", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            content = "".join("data: " + json.dumps(item) + "\n\n" for item in [chunk(delta, None), chunk({}, "tool_calls" if first else "stop")]) + "data: [DONE]\n\n"
            return httpx.Response(200, content=content.encode(), headers={"content-type": "text/event-stream"}, request=request)
        async def async_respond(client, request, **kwargs):
            return respond(request)
        def sync_respond(client, request, **kwargs):
            return respond(request)
        environment = {"OPENAI_API_BASE": "", "OPENAI_BASE_URL": "http://127.0.0.1:9090/v1", "OPENAI_API_KEY": "test-not-secret"}
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "probe.txt").write_text("wire-probe-ok", encoding="utf-8")
            with patch.dict(os.environ, environment), \
                 patch.object(httpx.AsyncClient, "send", async_respond), \
                 patch.object(httpx.Client, "send", sync_respond):
                runner = make_runner_factory(root, "openai:qwen-test")([], "read probe.txt")
                events = [event async for event in runner.stream()]
        self.assertEqual([event.payload for event in events if event.kind == "error"], [])
        self.assertEqual([path for path, body in requests], ["/v1/chat/completions"] * 2)
        returned = [message for message in requests[1][1]["messages"] if message["role"] == "tool"]
        self.assertEqual(len(returned), 1)
        self.assertEqual(returned[0]["tool_call_id"], "call-read")
        self.assertIn("wire-probe-ok", returned[0]["content"])
        self.assertEqual(events[-1].payload["status"], "completed")
        self.assertIn("已讀取 wire-probe-ok", events[-1].payload["output"])

    async def test_real_web_stream_executes_write_and_read_tool_calls(self):
        from tempfile import TemporaryDirectory
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage
        from pi_deepagent.agent import create_pi_deepagent as real_create

        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        model = ToolModel(responses=[
            AIMessage(content="開始測試工具", tool_calls=[{
                "name": "pi_write", "args": {"path": "sample.txt", "content": "工具測試內容"},
                "id": "write-1", "type": "tool_call",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "pi_read", "args": {"path": "sample.txt"},
                "id": "read-1", "type": "tool_call",
            }]),
            AIMessage(content="已讀回工具測試內容"),
        ])
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            def build(**kwargs):
                return real_create(workspace=kwargs["workspace"], model=model)
            with patch("pi_deepagent.web.runner.create_pi_deepagent", side_effect=build):
                runner = make_runner_factory(root, "fake:local")([], "試試工具")
                events = [event async for event in runner.stream()]
            self.assertEqual([event.payload for event in events if event.kind == "error"], [])
            self.assertEqual((root / "sample.txt").read_text(encoding="utf-8"), "工具測試內容")
            starts = [event.payload["name"] for event in events if event.kind == "tool_start"]
            self.assertEqual(starts, ["pi_write", "pi_read"])
            self.assertEqual(events[-1].payload["status"], "completed")
            self.assertIn("已讀回工具測試內容", events[-1].payload["output"])

    async def test_real_runtime_normalization_and_public_history_injection(self):
        graph = EventGraph()
        history = [{"role":"user","text":"prior"},{"role":"assistant","text":"reply"}]
        with patch("pi_deepagent.web.runner.create_pi_deepagent", return_value=graph) as create:
            runner = make_runner_factory(Path.cwd(), "openai:test")(history, "next")
            history.clear()
            events = [e async for e in runner.stream()]
        create.assert_called_once_with(workspace=Path.cwd(), model="openai:test")
        self.assertEqual(graph.inputs[0]["messages"], [
            {"role":"user","content":"prior"},{"role":"assistant","content":"reply"}, "next"])
        self.assertEqual([e.kind for e in events], ["run_start","model_start","model_delta","run_end"])
        self.assertEqual(events[-1].payload["status"], "completed")
        self.assertEqual(events[-1].payload["output"], "answer")

    async def test_factory_rejects_model_without_provider(self):
        with self.assertRaises(ValueError):
            make_runner_factory(Path.cwd(), "missing-provider")

    async def test_real_runtime_abort_emits_aborted_terminal(self):
        started = asyncio.Event()
        class BlockingGraph:
            async def astream_events(self, state, **kwargs):
                started.set()
                await asyncio.Event().wait()
                yield {}
        with patch("pi_deepagent.web.runner.create_pi_deepagent", return_value=BlockingGraph()):
            runner = make_runner_factory(Path.cwd(), "openai:test")([], "wait")
            async def collect():
                return [e async for e in runner.stream()]
            task = asyncio.create_task(collect())
            await asyncio.wait_for(started.wait(), 2)
            runner.abort()
            events = await asyncio.wait_for(task, 2)
        self.assertEqual(events[-1].kind, "run_end")
        self.assertEqual(events[-1].payload["status"], "aborted")

    async def test_real_deepagent_graph_accepts_version_context_and_history(self):
        from tempfile import TemporaryDirectory
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage
        from pi_deepagent.agent import create_pi_deepagent as real_create
        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self
        model = ToolModel(responses=[AIMessage(content="real graph answer")])
        with TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            def build(workspace, model):
                return real_create(workspace=workspace, model=fake_model)
            fake_model = model
            with patch("pi_deepagent.web.runner.create_pi_deepagent", side_effect=build):
                runner = make_runner_factory(root, "fake:local")(
                    [{"role":"user","text":"previous"},{"role":"assistant","text":"prior answer"}], "hello")
                events = [e async for e in runner.stream()]
            self.assertFalse([e for e in events if e.kind == "error"])
            self.assertEqual(events[-1].payload["status"], "completed")
            self.assertIn("real graph answer", events[-1].payload["output"])
            self.assertEqual([m.content for m in runner.runtime.message_history][-4:],
                             ["previous", "prior answer", "hello", "real graph answer"])
