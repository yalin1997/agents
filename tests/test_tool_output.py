"""Regression contracts for complete output and fail-closed native offload."""
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import WriteResult
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from pi_deepagent.agent import create_pi_deepagent
from pi_deepagent.tools import create_pi_tools
from tests.test_agent import ToolCapableFakeChatModel


def guard(root):
    with patch('deepagents.create_deep_agent', autospec=True) as create:
        create_pi_deepagent(root, model=ToolCapableFakeChatModel(responses=[AIMessage(content='done')]))
    return next(m for m in create.call_args.kwargs.get('middleware', [])
                if m.name == 'FilesystemMiddleware')


class CompleteCommandTests(TestCase):
    def test_unrelated_native_errors_are_not_reclassified(self):
        with TemporaryDirectory(dir=Path.cwd()) as root:
            middleware = guard(root)
            message = ToolMessage(content='small', name='custom', tool_call_id='small')
            error = ValueError('native processing error')
            with patch.object(FilesystemMiddleware, '_process_large_message', side_effect=error):
                with self.assertRaises(ValueError) as raised:
                    middleware._process_large_message(message, middleware.backend)
            self.assertIs(raised.exception, error)

    def test_single_long_line_and_command_messages_use_native_storage(self):
        with TemporaryDirectory(dir=Path.cwd()) as root:
            middleware = guard(root)
            text = '字' * 100000 + 'TAIL'
            original = ToolMessage(content=text, tool_call_id='long-line', name='custom')
            result = middleware._intercept_large_tool_result(Command(update={'messages':[original], 'other':7}))
            self.assertEqual(result.update['other'], 7)
            self.assertIn('/large_tool_results/long-line', result.update['messages'][0].content)
            self.assertEqual((Path(root)/'large_tool_results/long-line').read_text(encoding='utf-8'), text)

    def test_sync_graph_persistence_failure_does_not_reexecute_tool_or_call_model(self):
        calls = []
        def huge_output() -> str:
            """Return a large test output."""
            calls.append(True)
            return 'PRIVATE-OUTPUT' * 10000
        with TemporaryDirectory(dir=Path.cwd()) as root:
            model = ToolCapableFakeChatModel(responses=[
                AIMessage(content='', tool_calls=[{'name':'huge_output','args':{},'id':'failed','type':'tool_call'}]),
                AIMessage(content='must not be reached'),
            ])
            agent = create_pi_deepagent(root, model=model, extra_tools=[huge_output])
            with patch.object(FilesystemBackend, 'write', return_value=WriteResult(error='disk full')):
                with self.assertRaisesRegex(RuntimeError, 'tool_output_persistence_failed'):
                    agent.invoke({'messages':'get output'})
            self.assertEqual(calls, [True])
            self.assertEqual(model.i, 1)

    def test_success_failure_and_timeout_preserve_all_collected_text(self):
        stdout, stderr = '中文\n' * 30000 + 'STDOUT-END', 'error\n' * 10000 + 'STDERR-END'
        with TemporaryDirectory(dir=Path.cwd()) as root:
            bash = create_pi_tools(root)[3]
            for code in (0, 7):
                with self.subTest(code=code), patch('subprocess.run', autospec=True,
                        return_value=subprocess.CompletedProcess(['probe'], code, stdout, stderr)) as run:
                    expected = stdout + '\n[stderr]\n' + stderr
                    if code: expected += '\n[exit_code] 7'
                    self.assertEqual(bash('probe'), expected)
                    run.assert_called_once()
            with patch('subprocess.run', autospec=True,
                       side_effect=subprocess.TimeoutExpired(['probe'], 1, stdout.encode(), stderr.encode())):
                self.assertEqual(bash('probe', timeout=1), stdout + '\n[stderr]\n' + stderr
                                 + '\n[timeout] command timed out after 1 seconds')

    def test_native_offload_preserves_full_text_identity_and_small_results(self):
        with TemporaryDirectory(dir=Path.cwd()) as root:
            middleware = guard(root)
            backend = FilesystemBackend(root_dir=root, virtual_mode=True)
            small = ToolMessage(content='ok', tool_call_id='small', name='pi_bash')
            self.assertEqual(middleware._process_large_message(small, backend), (small, False))
            content = '中文\n' * 100000 + 'THE-END'
            original = ToolMessage(content=content, tool_call_id='big', name='pi_bash', status='success')
            result, saved = middleware._process_large_message(original, backend)
            self.assertTrue(saved)
            self.assertEqual((Path(root)/'large_tool_results/big').read_text(encoding='utf-8'), content)
            self.assertIn('offset', result.content)
            self.assertIn('limit', result.content)
            self.assertLess(len(result.content), 15000)
            self.assertEqual(result.tool_call_id, 'big')
            self.assertEqual(result.name, 'pi_bash')

    def test_write_failure_stops_instead_of_returning_original(self):
        with TemporaryDirectory(dir=Path.cwd()) as root:
            middleware = guard(root)
            backend = FilesystemBackend(root_dir=root, virtual_mode=True)
            message = ToolMessage(content='PRIVATE-OUTPUT' * 10000, name='pi_bash', tool_call_id='failed')
            for failure in (WriteResult(error='disk full PRIVATE-OUTPUT'), None, OSError('PRIVATE-OUTPUT')):
                kwargs = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
                with self.subTest(failure=type(failure).__name__), patch.object(backend, 'write', **kwargs):
                    with self.assertRaisesRegex(RuntimeError, 'tool_output_persistence_failed') as raised:
                        middleware._process_large_message(message, backend)
                    self.assertNotIn('PRIVATE-OUTPUT', str(raised.exception))
                    self.assertIn('failed', str(raised.exception))
                    self.assertIn('reason=OSError' if isinstance(failure, OSError) else 'reason=backend_write_failed', str(raised.exception))


class OutputGraphTests(IsolatedAsyncioTestCase):
    async def test_unrelated_async_native_errors_are_not_reclassified(self):
        with TemporaryDirectory(dir=Path.cwd()) as root:
            middleware = guard(root)
            message = ToolMessage(content='small', name='custom', tool_call_id='small')
            error = ValueError('native processing error')
            with patch.object(FilesystemMiddleware, '_aprocess_large_message', side_effect=error):
                with self.assertRaises(ValueError) as raised:
                    await middleware._aprocess_large_message(message, middleware.backend)
            self.assertIs(raised.exception, error)

    async def test_async_backend_failures_never_return_large_original(self):
        with TemporaryDirectory(dir=Path.cwd()) as root:
            middleware = guard(root)
            backend = FilesystemBackend(root_dir=root, virtual_mode=True)
            small = ToolMessage(content='small', name='custom', tool_call_id='small')
            self.assertEqual(await middleware._aprocess_large_message(small, backend), (small, False))
            original = ToolMessage(content='PRIVATE-OUTPUT'*10000, name='custom', tool_call_id='failed')
            for failure in (None, WriteResult(error='PRIVATE-OUTPUT'), OSError('PRIVATE-OUTPUT')):
                kwargs = {'side_effect':failure} if isinstance(failure, Exception) else {'return_value':failure}
                with self.subTest(failure=type(failure).__name__), patch.object(backend, 'awrite', **kwargs):
                    with self.assertRaisesRegex(RuntimeError, 'tool_output_persistence_failed') as raised:
                        await middleware._aprocess_large_message(original, backend)
                    self.assertNotIn('PRIVATE-OUTPUT', str(raised.exception))
                    self.assertIn('reason=OSError' if isinstance(failure, OSError) else 'reason=backend_write_failed', str(raised.exception))

    async def test_agent_reads_middle_of_saved_command_without_reexecuting_it(self):
        with TemporaryDirectory(dir=Path.cwd()) as root:
            executable = Path(sys.executable).as_posix()
            command = f'"{executable}" -c "print(\'prefix\\n\'*20000 + \'MIDDLE-TARGET\\n\' + \'suffix\\n\'*20000, end=\'\')"'
            model = ToolCapableFakeChatModel(responses=[
                AIMessage(content='', tool_calls=[{'name':'pi_bash','args':{'command':command},'id':'bash-big','type':'tool_call'}]),
                AIMessage(content='', tool_calls=[{'name':'read_file','args':{'file_path':'/large_tool_results/bash-big','offset':20000,'limit':1},'id':'read-middle','type':'tool_call'}]),
                AIMessage(content='MIDDLE-TARGET'),
            ])
            result = await create_pi_deepagent(root, model=model).ainvoke({'messages':'read middle'})
            tools = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            self.assertEqual([m.name for m in tools], ['pi_bash', 'read_file'])
            self.assertIn('saved in the filesystem', tools[0].content)
            self.assertNotIn('MIDDLE-TARGET', tools[0].content)
            self.assertIn('MIDDLE-TARGET', tools[1].content)
            self.assertEqual((Path(root)/'large_tool_results/bash-big').read_text(encoding='utf-8'),
                             'prefix\n'*20000 + 'MIDDLE-TARGET\n' + 'suffix\n'*20000)

    async def test_async_write_failure_stops_before_another_model_call(self):
        with TemporaryDirectory(dir=Path.cwd()) as root:
            def huge_output() -> str:
                """Return a large test output."""
                return 'PRIVATE-OUTPUT' * 10000
            model = ToolCapableFakeChatModel(responses=[
                AIMessage(content='', tool_calls=[{'name':'huge_output','args':{},'id':'failed','type':'tool_call'}]),
                AIMessage(content='must not be reached'),
            ])
            agent = create_pi_deepagent(root, model=model, extra_tools=[huge_output])
            with patch.object(FilesystemBackend, 'awrite', return_value=WriteResult(error='disk full')) as write:
                with self.assertRaisesRegex(RuntimeError, 'tool_output_persistence_failed'):
                    await agent.ainvoke({'messages':'get output'})
                self.assertEqual(write.await_count, 1)
            self.assertEqual(model.i, 1)
