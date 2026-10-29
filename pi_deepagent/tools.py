"""Pi-style local tools for Deep Agents.

The public functions operate on the process working directory for backwards
compatibility. ``create_pi_tools`` is used by the agent factory so every tool
is explicitly bound to the requested workspace instead of relying on the
process working directory.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ToolAdapter(Protocol):
    """Framework-neutral tool boundary used by the future tool registry."""

    @property
    def name(self) -> str: ...

    def invoke(self, arguments: dict[str, Any], *, context: Any = None) -> Any: ...


MAX_READ_CHARS = 50_000
DEFAULT_COMMAND_TIMEOUT = 120


def resolve_workspace(workspace: str | Path) -> Path:
    """Resolve and validate a workspace directory."""
    root = Path(workspace).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"Workspace does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Workspace is not a directory: {root}")
    return root


def _workspace() -> Path:
    return resolve_workspace(Path.cwd())


def _resolve(path: str, root: Path) -> Path:
    candidate = Path(path)
    target = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {path}") from exc
    return target


def _display_path(target: Path, root: Path) -> str:
    relative = target.relative_to(root).as_posix()
    return relative or "."


def _read_file(root: Path, path: str, offset: int, limit: int) -> str:
    try:
        target = _resolve(path, root)
    except (OSError, RuntimeError, ValueError) as exc:
        return f"Error: {exc}"

    if not target.exists():
        return f"File not found: {path}"
    if target.is_dir():
        return f"Path is a directory: {path}"

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Error reading {path}: {exc}"

    lines = text.splitlines()
    start = max(offset - 1, 0)
    end = min(start + max(limit, 1), len(lines))
    result = "\n".join(lines[start:end])
    if len(result) > MAX_READ_CHARS:
        result = result[:MAX_READ_CHARS] + "\n...[truncated]"
    return result if result else "(empty file)"


def pi_read(path: str, offset: int = 1, limit: int = 2000) -> str:
    """Read a UTF-8 text file with a 1-based line offset.

    Paths must stay inside the current workspace. ``offset`` and ``limit``
    are line-based, and the returned text is additionally capped in size.
    """
    return _read_file(_workspace(), path, offset, limit)


def _write_file(root: Path, path: str, content: str) -> str:
    try:
        target = _resolve(path, root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except (OSError, RuntimeError, ValueError) as exc:
        return f"Error writing {path}: {exc}"
    return f"Wrote {len(content.encode('utf-8'))} bytes to {_display_path(target, root)}"


def pi_write(path: str, content: str) -> str:
    """Create or overwrite a UTF-8 text file inside the current workspace."""
    return _write_file(_workspace(), path, content)


def _edit_file(root: Path, path: str, old_text: str, new_text: str, replace_all: bool) -> str:
    if not old_text:
        return "Error: old_text must not be empty"
    if old_text == new_text:
        return "Error: new_text is identical to old_text"

    try:
        target = _resolve(path, root)
    except (OSError, RuntimeError, ValueError) as exc:
        return f"Error: {exc}"
    if not target.exists():
        return f"File not found: {path}"
    if target.is_dir():
        return f"Path is a directory: {path}"

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Error reading {path}: {exc}"

    count = text.count(old_text)
    if count == 0:
        return "old_text was not found"
    if count > 1 and not replace_all:
        return f"old_text matched {count} times; set replace_all=True or provide a more specific old_text"

    updated = text.replace(old_text, new_text, -1 if replace_all else 1)
    try:
        target.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return f"Error writing {path}: {exc}"
    replacements = count if replace_all else 1
    return f"Edited {_display_path(target, root)} ({replacements} replacement(s))"


def pi_edit(path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
    """Edit a file by exact text replacement; by default the match must be unique."""
    return _edit_file(_workspace(), path, old_text, new_text, replace_all)


def _combine_output(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    def as_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    output = as_text(stdout)
    error = as_text(stderr)
    if error:
        output += ("\n[stderr]\n" if output else "[stderr]\n") + error
    return output or "(no output)"


def _bash(root: Path, command: str, timeout: int) -> str:
    if not command.strip():
        return "Error: No command provided"
    if timeout <= 0:
        return f"Error: timeout must be positive, got {timeout}"

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return f"Error: Could not parse command: {exc}"
    if not argv:
        return "Error: No command provided"

    try:
        completed = subprocess.run(
            argv,
            cwd=root,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        output = _combine_output(exc.stdout, exc.stderr)
        return f"{output}\n[timeout] command timed out after {timeout} seconds"
    except FileNotFoundError:
        return f"Error: Command not found: {argv[0]}"
    except PermissionError as exc:
        return f"Error: Permission denied while running {argv[0]}: {exc}"
    except OSError as exc:
        return f"Error running command: {exc}"

    output = _combine_output(completed.stdout, completed.stderr)
    if completed.returncode != 0:
        output += f"\n[exit_code] {completed.returncode}"
    return output


def pi_bash(command: str, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> str:
    """Run one command without a shell in the current workspace.

    This intentionally uses argument parsing plus ``shell=False``. Pipelines,
    redirects, and compound commands require the Deep Agents ``execute`` tool,
    which is available when the agent is created with ``shell=True``.
    """
    return _bash(_workspace(), command, timeout)


def _named_tool(function: Callable[..., str], name: str, docstring: str) -> Callable[..., str]:
    """Give a workspace-bound closure the stable name LangChain exposes."""
    function.__name__ = name
    function.__qualname__ = name
    function.__doc__ = docstring
    return function


def create_pi_tools(workspace: str | Path) -> list[Callable[..., str]]:
    """Create Pi tools bound to ``workspace``.

    Binding the root in a closure keeps it out of the model-visible tool schema
    and prevents ``--workspace`` from silently depending on the process cwd.
    """
    root = resolve_workspace(workspace)

    def read(path: str, offset: int = 1, limit: int = 2000) -> str:
        return _read_file(root, path, offset, limit)

    def write(path: str, content: str) -> str:
        return _write_file(root, path, content)

    def edit(path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
        return _edit_file(root, path, old_text, new_text, replace_all)

    def bash(command: str, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> str:
        return _bash(root, command, timeout)

    return [
        _named_tool(read, "pi_read", pi_read.__doc__ or "Read a text file."),
        _named_tool(write, "pi_write", pi_write.__doc__ or "Write a text file."),
        _named_tool(edit, "pi_edit", pi_edit.__doc__ or "Edit a text file."),
        _named_tool(bash, "pi_bash", pi_bash.__doc__ or "Run a command."),
    ]


PI_TOOLS = [pi_read, pi_write, pi_edit, pi_bash]

__all__ = [
    "DEFAULT_COMMAND_TIMEOUT",
    "MAX_READ_CHARS",
    "PI_TOOLS",
    "ToolAdapter",
    "create_pi_tools",
    "pi_bash",
    "pi_edit",
    "pi_read",
    "pi_write",
    "resolve_workspace",
]
