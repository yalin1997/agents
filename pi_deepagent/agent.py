"""Factory for a Pi-inspired Deep Agent."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import import_module
from ipaddress import ip_address
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from langchain_core.language_models import BaseChatModel

def _configure_local_model(model: str | BaseChatModel) -> str | BaseChatModel:
    """Use Chat Completions for loopback OpenAI-compatible servers.

    Deep Agents defaults openai: strings to Responses, whose reasoning-item
    round trip is not supported by all local servers. Explicit model objects
    and non-local providers retain their chosen protocol.
    """
    if not isinstance(model, str) or not model.startswith("openai:"):
        return model
    endpoint = os.environ.get("OPENAI_API_BASE") or os.environ.get("OPENAI_BASE_URL")
    if not endpoint:
        return model
    try:
        host = urlsplit(endpoint).hostname
        local = host == "localhost" or (host is not None and ip_address(host).is_loopback)
    except ValueError:
        return model
    if not local:
        return model
    from .local_model import make_local_model

    return make_local_model(model.split(':', 1)[1], endpoint)


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
    system_prompt: str | None = None,
    response_format: Any = None,
    include_pi_tools: bool = True,
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
        system_prompt: Override the default Pi prompt for a specialized agent.
        response_format: Optional Deep Agents structured output configuration.
        include_pi_tools: Include workspace coding tools (disable for remote workers).
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

    options: dict[str, Any] = {}
    if response_format is not None:
        options["response_format"] = response_format
    configured_model = _configure_local_model(model)
    if configured_model is not model:
        from .local_model import native_context_middleware
        options['middleware'] = native_context_middleware(configured_model, backend)
    else:
        from .tool_output import SafeFilesystemMiddleware
        options['middleware'] = [SafeFilesystemMiddleware(backend=backend)]
    return create_deep_agent(
        model=configured_model,
        tools=[*(create_pi_tools(root) if include_pi_tools else []), *(extra_tools or [])],
        system_prompt=system_prompt if system_prompt is not None else build_system_prompt(root),
        backend=backend,
        subagents=subagents,
        **options,
    )
