"""Per-graph team tool restrictions using public middleware hooks.

The executor/backend remains responsible for OS isolation and current policy.
This boundary removes delegation tools and rejects forged calls without
changing a global model profile or another single-agent graph.
"""
from __future__ import annotations

import posixpath

from langchain.agents.middleware import AgentMiddleware


PRIVATE_ARTIFACT_ROOT = "/.framework"
DELEGATION_TOOLS = frozenset({"task", "launch_async_task", "check_async_task", "update_async_task",
                              "cancel_async_task", "list_async_tasks"})
HOST_TOOLS = frozenset({"pi_bash"})
FILE_WRITES = {"write_file": "file_path", "edit_file": "file_path", "delete_file": "file_path",
               "pi_write": "path", "pi_edit": "path"}


class TeamToolAccessError(PermissionError):
    """A tool request attempts to leave the registered team boundary."""


class TeamToolBoundary(AgentMiddleware):
    def _visible(self, request):
        def name(tool):
            return tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", "")
        return request.override(tools=[tool for tool in request.tools
                                       if name(tool) not in DELEGATION_TOOLS | HOST_TOOLS])

    def wrap_model_call(self, request, handler):
        return handler(self._visible(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._visible(request))

    def _check(self, request):
        call = request.tool_call
        name = call["name"]
        if name in DELEGATION_TOOLS | HOST_TOOLS:
            raise TeamToolAccessError("team_tool_not_allowed")
        key = FILE_WRITES.get(name)
        if key:
            path = call.get("args", {}).get(key)
            if not isinstance(path, str) or "\\" in path or "\0" in path:
                raise TeamToolAccessError("invalid_team_file_path")
            normalized = "/" + posixpath.normpath("/" + path.lstrip("/")).lstrip("/")
            if normalized == PRIVATE_ARTIFACT_ROOT or normalized.startswith(PRIVATE_ARTIFACT_ROOT + "/"):
                raise TeamToolAccessError("framework_artifact_read_only")

    def wrap_tool_call(self, request, handler):
        self._check(request)
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        self._check(request)
        return await handler(request)
