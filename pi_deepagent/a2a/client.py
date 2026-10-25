"""SDK discovery and transport, with snapshot reconciliation after SSE."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_artifact_text, get_message_text
from a2a.types import (
    CancelTaskRequest, GetTaskRequest, ListTasksRequest, Message, Part, Role,
    SendMessageConfiguration, SendMessageRequest, Task, TaskState,
)
from google.protobuf.json_format import MessageToDict

WAITING = {TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED}
TERMINAL = {
    TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_REJECTED, TaskState.TASK_STATE_CANCELED,
}


def task_result(task: Task) -> dict:
    if task.status.state == TaskState.TASK_STATE_COMPLETED and not any(
        get_artifact_text(artifact, delimiter="\n").strip() for artifact in task.artifacts
    ):
        raise ValueError("Remote task is completed but has no result artifacts")
    return {
        "task_id": task.id, "context_id": task.context_id,
        "state": TaskState.Name(task.status.state),
        "message": get_message_text(task.status.message, delimiter="\n"),
        "artifacts": [
            {"artifact_id": item.artifact_id, "name": item.name,
             "text": get_artifact_text(item, delimiter="\n")}
            for item in task.artifacts
        ],
    }


class RemoteClient:
    """No automatic mutation retries: a timeout may have accepted the message."""

    def __init__(self, url: str, *, streaming: bool = True,
                 timeout: float = 200, http: httpx.AsyncClient | None = None) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Agent URL must be HTTP(S), without embedded credentials")
        self.url = url.rstrip("/")
        self.streaming = streaming
        self.timeout = timeout
        self.http = http
        self._owns_http = http is None
        self.client = None
        self._nonstreaming = None
        self.card = None

    async def __aenter__(self) -> RemoteClient:
        if self.http is None:
            self.http = httpx.AsyncClient(timeout=self.timeout, follow_redirects=False, trust_env=False)
        try:
            self.card = await A2ACardResolver(self.http, self.url).get_agent_card()
            supported = [i for i in self.card.supported_interfaces
                         if i.protocol_binding == "JSONRPC" and i.protocol_version == "1.0"]
            if not supported:
                raise ValueError("Agent does not advertise A2A 1.0 JSONRPC")
            expected = urlsplit(self.url)
            for interface in supported:
                actual = urlsplit(interface.url)
                if (actual.scheme, actual.hostname, actual.port) != (expected.scheme, expected.hostname, expected.port):
                    raise ValueError("Agent Card redirects transport to a different origin")
            self.client = await create_client(self.card, client_config=ClientConfig(
                httpx_client=self.http, supported_protocol_bindings=["JSONRPC"],
                streaming=self.streaming, accepted_output_modes=["text/plain"],
            ))
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *args) -> None:
        if self.client is not None:
            await self.client.close()
        if self._owns_http and self.http is not None:
            await self.http.aclose()

    async def get(self, task_id: str) -> Task:
        return await self.client.get_task(GetTaskRequest(id=task_id))

    async def cancel(self, task_id: str) -> Task:
        return await self.client.cancel_task(CancelTaskRequest(id=task_id))

    async def list(self) -> dict:
        return MessageToDict(await self.client.list_tasks(ListTasksRequest(include_artifacts=True)))

    async def send(self, text: str, *, task_id: str = "", context_id: str = "",
                   message_id: str | None = None, return_immediately: bool = False,
                   on_task: Callable | None = None) -> Task:
        if not text.strip() or len(text) > 32000:
            raise ValueError("Text must contain 1..32000 characters")
        if task_id:
            current = await self.get(task_id)
            if current.status.state not in WAITING:
                raise ValueError("Only a task waiting for input/auth can receive a continuation")
            if context_id and current.context_id != context_id:
                raise ValueError("Task/context mismatch")
            context_id = current.context_id
        request = SendMessageRequest(
            message=Message(
                message_id=message_id or str(uuid4()), role=Role.ROLE_USER,
                parts=[Part(text=text)], task_id=task_id, context_id=context_id,
            ),
            configuration=SendMessageConfiguration(return_immediately=return_immediately),
        )
        observed_id = task_id
        observed_context = context_id

        async def observe(task: Task) -> None:
            nonlocal observed_id, observed_context
            if observed_id and task.id != observed_id:
                raise ValueError("Remote task identity changed")
            if observed_context and task.context_id != observed_context:
                raise ValueError("Remote context identity changed")
            observed_id, observed_context = task.id, task.context_id
            if on_task:
                value = on_task(task)
                if inspect.isawaitable(value):
                    await value

        # An overall bound also limits endless event streams and polling.
        async with asyncio.timeout(self.timeout):
            sender = self.client
            if return_immediately and self.streaming:
                if self._nonstreaming is None:
                    # Shares the HTTP pool, closed by the primary client.
                    self._nonstreaming = await create_client(self.card, client_config=ClientConfig(
                        httpx_client=self.http, supported_protocol_bindings=["JSONRPC"], streaming=False,
                    ))
                sender = self._nonstreaming
            async for event in sender.send_message(request):
                if event.HasField("message"):
                    raise ValueError("This task-based worker unexpectedly returned a direct Message")
                if event.HasField("task"):
                    await observe(event.task)
                else:
                    update = event.status_update if event.HasField("status_update") else event.artifact_update
                    if not observed_id or update.task_id != observed_id or update.context_id != observed_context:
                        raise ValueError("SSE event does not match the task/context")
            if not observed_id:
                raise ValueError("Remote server returned no task")
            # Never concatenate replayed artifact chunks or infer completion
            # from EOF. The stored Task is the authoritative final snapshot.
            while True:
                snapshot = await self.get(observed_id)
                await observe(snapshot)
                if return_immediately or snapshot.status.state in TERMINAL | WAITING:
                    return snapshot
                await asyncio.sleep(0.25)
