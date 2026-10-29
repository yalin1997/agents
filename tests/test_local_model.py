import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from langchain_core.exceptions import ContextOverflowError
from langchain_openai import ChatOpenAI

from pi_deepagent.local_model import make_local_model, native_context_middleware


class ProviderError(Exception):
    def __init__(self, body):
        super().__init__('local provider rejected request')
        self.body = body


def model():
    with patch.dict(os.environ, {'OPENAI_API_KEY':'offline-only','PI_DEEPAGENT_CONTEXT_WINDOW':'16384'}):
        return make_local_model('qwen-test','http://127.0.0.1:9090/v1')


class NativeContextTests(TestCase):
    def test_config_sets_model_profile_and_uses_only_native_middleware(self):
        from deepagents.backends import FilesystemBackend
        from deepagents.middleware.filesystem import FilesystemMiddleware
        from deepagents.middleware.summarization import SummarizationMiddleware, SummarizationToolMiddleware
        configured = model()
        self.assertEqual(configured.profile['max_input_tokens'],13312)
        self.assertEqual(configured.max_tokens,2048)
        self.assertFalse(configured.use_responses_api)
        with TemporaryDirectory(dir=Path.cwd()) as temporary:
            middleware = native_context_middleware(configured,FilesystemBackend(root_dir=temporary,virtual_mode=True))
        from pi_deepagent.tool_output import SafeFilesystemMiddleware
        self.assertEqual([type(m) for m in middleware],[SafeFilesystemMiddleware,SummarizationMiddleware,SummarizationToolMiddleware])
        self.assertIsInstance(middleware[0], FilesystemMiddleware)
        self.assertIs(middleware[2]._summarization,middleware[1])
        self.assertEqual(middleware[2].tools[0].name,'compact_conversation')
        self.assertEqual(middleware[1]._lc_helper.trim_tokens_to_summarize,4000)

    def test_configuration_can_change_capacity_and_rejects_invalid_values(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'offline-only','PI_DEEPAGENT_CONTEXT_WINDOW':'32768'}):
            configured = make_local_model('qwen-test','http://127.0.0.1:9090/v1')
        self.assertEqual(configured.profile['max_input_tokens'],29696)
        for value in ('1024','invalid'):
            with self.subTest(value=value), patch.dict(os.environ,{'PI_DEEPAGENT_CONTEXT_WINDOW':value}):
                with self.assertRaises(ValueError): make_local_model('qwen-test','http://127.0.0.1:9090/v1')

    def test_sync_generation_and_late_stream_errors_are_normalized_without_retry(self):
        configured = model()
        error = ProviderError({'error':{'type':'exceed_context_size_error'}})
        with patch.object(ChatOpenAI,'_generate',side_effect=error) as generate:
            with self.assertRaises(ContextOverflowError) as raised: configured._generate([])
        self.assertIs(raised.exception.__cause__,error)
        generate.assert_called_once()
        closed = []
        def stream(*args, **kwargs):
            try:
                yield 'first'
                raise error
            finally:
                closed.append(True)
        with patch.object(ChatOpenAI,'_stream',stream):
            iterator = configured._stream([])
            self.assertEqual(next(iterator),'first')
            with self.assertRaises(ContextOverflowError): next(iterator)
        self.assertEqual(closed,[True])

    def test_non_context_errors_are_preserved(self):
        configured = model()
        for error in (ValueError('other'), ProviderError({'type':'authentication_error'}), ProviderError(None)):
            with self.subTest(error=error), patch.object(ChatOpenAI,'_generate',side_effect=error) as generate:
                with self.assertRaises(type(error)) as raised: configured._generate([])
                self.assertIs(raised.exception,error)
                generate.assert_called_once()


class AsyncCompatibilityTests(IsolatedAsyncioTestCase):
    async def test_async_generation_and_late_stream_errors_are_normalized(self):
        configured = model()
        error = ProviderError({'type':'exceed_context_size_error'})
        with patch.object(ChatOpenAI,'_agenerate',side_effect=error) as generate:
            with self.assertRaises(ContextOverflowError): await configured._agenerate([])
        generate.assert_awaited_once()
        closed = []
        async def stream(*args, **kwargs):
            try:
                yield 'first'
                raise error
            finally:
                closed.append(True)
        with patch.object(ChatOpenAI,'_astream',stream):
            iterator = configured._astream([])
            self.assertEqual(await anext(iterator),'first')
            with self.assertRaises(ContextOverflowError): await anext(iterator)
        self.assertEqual(closed,[True])
