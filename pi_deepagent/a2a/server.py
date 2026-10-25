"""Official SDK AgentExecutor, task store, routing, and local server lifecycle."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import TaskUpdater
from a2a.server.tasks.database_task_store import DatabaseTaskStore
from a2a.types import (
    AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Message, Part, Role,
    Task, TaskState, TaskStatus,
)
from a2a.utils.errors import InvalidParamsError
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.applications import Starlette

from .worker import Worker, conversation_messages

logger = logging.getLogger(__name__)


class PiAgentExecutor(AgentExecutor):
    def __init__(self, worker: Worker, *, timeout: float = 180) -> None:
        self.worker = worker
        self.timeout = timeout

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if not context.message or not context.get_user_input().strip():
            raise InvalidParamsError(message="A non-empty text message is required")
        if len(context.get_user_input()) > 32000:
            raise InvalidParamsError(message="Text exceeds the 32000 character limit")
        if any(part.WhichOneof("content") != "text" for part in context.message.parts):
            raise InvalidParamsError(message="This agent accepts text parts only")
        task_id, context_id = context.task_id, context.context_id
        if not task_id or not context_id:
            raise InvalidParamsError(message="Missing task/context identity")
        if context.current_task is None:
            await event_queue.enqueue_event(Task(
                id=task_id, context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[context.message],
            ))
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.start_work()
        try:
            async with asyncio.timeout(self.timeout):
                outcome = await self.worker.run(conversation_messages(context.current_task, context.message))
            message = updater.new_agent_message(parts=[Part(text=outcome.text)])
            if outcome.outcome == "input_required":
                await updater.requires_input(message)
            elif outcome.outcome == "rejected":
                await updater.reject(message)
            else:
                await updater.add_artifact(
                    parts=[Part(text=outcome.result)], name="result", last_chunk=True,
                )
                await updater.complete(message)
        except asyncio.CancelledError:
            # SDK calls cancel() and commits the cancellation state.
            raise
        except Exception:
            logger.exception("Remote worker failed (task=%s)", task_id)
            await updater.failed(updater.new_agent_message(parts=[Part(
                text="Agent execution failed. Check the server log using the task ID.",
            )]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id or "", context.context_id or "")
        await updater.cancel()


def make_card(base_url: str) -> AgentCard:
    return AgentCard(
        name="Pi Remote Assistant", version="1.0.0",
        description="Single-hop research and writing assistant with multi-turn clarification.",
        supported_interfaces=[AgentInterface(
            url=f"{base_url.rstrip('/')}/a2a/jsonrpc",
            protocol_binding="JSONRPC", protocol_version="1.0",
        )],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text/plain"], default_output_modes=["text/plain"],
        skills=[AgentSkill(
            id="research-writing", name="Research and writing",
            description="Answer or write; ask for essential missing information before completion.",
            tags=["research", "writing", "clarification"],
            examples=["Write an invitation. Ask me for essential missing details."],
        )],
    )


def create_app(worker: Worker, *, database: str | Path, base_url: str,
               timeout: float = 180) -> Starlette:
    """One process/worker. Durable task history uses the official SQL task store."""
    database = Path(database).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine("sqlite+aiosqlite:///" + database.as_posix())
    store = DatabaseTaskStore(engine)
    card = make_card(base_url)
    handler = DefaultRequestHandler(
        agent_executor=PiAgentExecutor(worker, timeout=timeout),
        task_store=store, agent_card=card,
    )

    @asynccontextmanager
    async def lifespan(app: Starlette):
        await store.initialize()
        # An in-flight computation cannot survive a process restart. Never leave
        # it looking active forever or silently rerun possible side effects.
        from a2a.server.context import ServerCallContext
        from a2a.types import ListTasksRequest
        call_context = ServerCallContext()
        for state in (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING):
            while True:
                page = await store.list(ListTasksRequest(status=state, page_size=100), call_context)
                if not page.tasks:
                    break
                for task in page.tasks:
                    task.status.state = TaskState.TASK_STATE_FAILED
                    task.status.timestamp.GetCurrentTime()
                    task.status.message.CopyFrom(Message(
                        message_id=str(uuid4()), role=Role.ROLE_AGENT,
                        task_id=task.id, context_id=task.context_id,
                        parts=[Part(text="Server restarted during execution; submit a new task.")],
                    ))
                    await store.save(task, call_context)
        try:
            yield
        finally:
            await engine.dispose()

    app = Starlette(
        routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/a2a/jsonrpc")],
        lifespan=lifespan,
    )
    app.state.task_store = store
    app.state.request_handler = handler
    return app
