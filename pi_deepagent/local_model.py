"""Local provider compatibility and configuration of native context tools."""
from contextlib import aclosing, contextmanager
from functools import partial
import os

from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages.utils import count_tokens_approximately
from langchain_openai import ChatOpenAI


@contextmanager
def _context_errors():
    """Normalize llama.cpp's spelling; native middleware owns recovery."""
    try:
        yield
    except Exception as exc:
        body = getattr(exc, 'body', None)
        error = body.get('error', body) if isinstance(body, dict) else None
        if isinstance(error, dict) and error.get('type') == 'exceed_context_size_error':
            raise ContextOverflowError(str(exc)) from exc
        raise


class LocalChatOpenAI(ChatOpenAI):
    def _generate(self, *args, **kwargs):
        with _context_errors():
            return super()._generate(*args, **kwargs)

    async def _agenerate(self, *args, **kwargs):
        with _context_errors():
            return await super()._agenerate(*args, **kwargs)

    def _stream(self, *args, **kwargs):
        with _context_errors():
            yield from super()._stream(*args, **kwargs)

    async def _astream(self, *args, **kwargs):
        with _context_errors():
            async with aclosing(super()._astream(*args, **kwargs)) as stream:
                async for chunk in stream:
                    yield chunk


def make_local_model(name, endpoint):
    capacity = int(os.environ.get('PI_DEEPAGENT_CONTEXT_WINDOW', '16384'))
    if capacity < 4096:
        raise ValueError('PI_DEEPAGENT_CONTEXT_WINDOW 必須至少為 4096。')
    output = min(2048, capacity // 8)
    return LocalChatOpenAI(model=name, base_url=endpoint, use_responses_api=False,
                           max_tokens=output,
                           profile={'max_input_tokens':capacity-output-1024,
                                    'max_output_tokens':output, 'tool_calling':True})


def native_context_middleware(model, backend):
    from .tool_output import SafeFilesystemMiddleware
    from deepagents.middleware.summarization import (
        SummarizationToolMiddleware, create_summarization_middleware,
    )
    budget = model.profile['max_input_tokens']
    summary = create_summarization_middleware(
        model, backend, trim_tokens_to_summarize=min(4000, budget//3),
        token_counter=partial(count_tokens_approximately, chars_per_token=2),
    )
    return [SafeFilesystemMiddleware(backend=backend,
                                tool_token_limit_before_evict=max(256, budget//12),
                                human_message_token_limit_before_evict=max(256, budget//4)),
            summary, SummarizationToolMiddleware(summary)]
