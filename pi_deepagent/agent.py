"""Factory for a Pi-inspired Deep Agent."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import import_module
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel

def create_pi_deepagent(
    workspace: str | Path = ".",
    model: str | BaseChatModel = "google_genai:gemini-3.6-flash",
    *,
    shell: bool = False,
    virtual_mode: bool = True,
    inherit_env: bool = True,
    shell_timeout: int = 120,
    extra_tools: Sequence[Any] | None = None,
    subagents: Sequence[dict[str, Any]] | None = None,
):
    """Create a Deep Agents coding assistant modeled after Pi Coding Agent.

    Args:
        workspace: Root directory the agent can access.
        model: LangChain model identifier, e.g. ``openai:gpt-5.5`` or
            ``google_genai:gemini-3.6-flash``.
        shell: If true, use LocalShellBackend to enable Deep Agents' built-in
            execute tool. This is not sandboxed.
        virtual_mode: Keep filesystem paths normalized under workspace when
            using FilesystemBackend.
        inherit_env: If true, let the local shell inherit the parent process
            environment. This keeps commands such as ``python`` and ``rg``
            available on the host. It only applies when ``shell=True``.
        shell_timeout: Default timeout in seconds for the built-in execute
            tool when ``shell=True``.
        extra_tools: Additional LangChain-compatible tools/callables.
        subagents: Optional Deep Agents subagent specs.
    """
    deepagents = import_module("deepagents")
    backends = import_module("deepagents.backends")
    prompt_module = import_module("pi_deepagent.prompt")
    tools_module = import_module("pi_deepagent.tools")
    create_deep_agent = deepagents.create_deep_agent
    filesystem_backend = backends.FilesystemBackend
    local_shell_backend = backends.LocalShellBackend
    build_system_prompt = prompt_module.build_system_prompt
    create_pi_tools = tools_module.create_pi_tools
    resolve_workspace = tools_module.resolve_workspace

    root = resolve_workspace(workspace)
    backend = (
        local_shell_backend(
            root_dir=str(root),
            inherit_env=inherit_env,
            timeout=shell_timeout,
        )
        if shell
        else filesystem_backend(root_dir=str(root), virtual_mode=virtual_mode)
    )

    return create_deep_agent(
        model=model,
        tools=[*create_pi_tools(root), *(extra_tools or [])],
        system_prompt=build_system_prompt(root),
        backend=backend,
        subagents=subagents,
    )
