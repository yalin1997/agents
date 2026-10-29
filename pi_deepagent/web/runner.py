"""Public graph adapter for a single web run with explicit durable history."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import traceback

from pi_deepagent.agent import create_pi_deepagent
from pi_deepagent.contracts import ModelRef
from pi_deepagent.runtime import AgentLoopRuntime


class _HistoryGraph:
    def __init__(self, graph: Any, history: list[dict]) -> None:
        self.graph = graph
        self.history = [{"role": m["role"], "content": m["text"]} for m in deepcopy(history)]
        self._streams = set()
        self.error_traceback = ''

    def astream_events(self, state: dict, **kwargs: Any):
        # Runtime does not close its nested native iterator on early exit.
        # Retain ownership here so the web runner can drain every layer.
        iterator = self._events(state, kwargs)
        self._streams.add(iterator)
        return iterator

    async def _events(self, state: dict, kwargs: dict):
        state = {**state, "messages": [*self.history, *state["messages"]]}
        stream = self.graph.astream_events(state, **kwargs)
        try:
            async for event in stream:
                yield event
        except Exception as exc:
            self.error_traceback = '\n'.join(f'{f.filename}:{f.lineno} in {f.name}'
                                             for f in traceback.extract_tb(exc.__traceback__))
            raise
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()

    async def aclose(self):
        for iterator in list(self._streams):
            try:
                await iterator.aclose()
            finally:
                self._streams.discard(iterator)


class WebRunner:
    def __init__(self, runtime: AgentLoopRuntime, prompt: str, graph: _HistoryGraph) -> None:
        self.runtime = runtime
        self.prompt = prompt
        self.graph = graph

    async def stream(self):
        events = self.runtime.stream(self.prompt)
        try:
            async for event in events:
                yield event
        finally:
            try:
                await events.aclose()
            finally:
                await self.graph.aclose()

    def abort(self) -> None:
        self.runtime.abort()

    @property
    def error_traceback(self):
        return self.graph.error_traceback


def make_runner_factory(workspace: Path, model: str):
    provider, separator, name = model.partition(":")
    if not separator or not provider.strip() or not name.strip():
        raise ValueError("model must include provider:model")
    model_ref = ModelRef(provider=provider, model_id=name)
    workspace = Path(workspace).resolve()

    def factory(history: list[dict], prompt: str) -> WebRunner:
        graph = create_pi_deepagent(workspace=workspace, model=model)
        adapter = _HistoryGraph(graph, history)
        return WebRunner(AgentLoopRuntime(adapter, model=model_ref, workspace=workspace), prompt, adapter)
    return factory
