from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pi_deepagent.tools import create_pi_tools, resolve_workspace


class WorkspaceToolsTests(TestCase):
    def test_tools_are_bound_to_the_requested_workspace(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tools = {tool.__name__: tool for tool in create_pi_tools(root)}

            self.assertEqual(
                tools["pi_write"]("nested/file.txt", "hello\nworld"),
                "Wrote 11 bytes to nested/file.txt",
            )
            self.assertEqual(tools["pi_read"]("nested/file.txt"), "hello\nworld")
            self.assertEqual(
                tools["pi_edit"]("nested/file.txt", "world", "agent"),
                "Edited nested/file.txt (1 replacement(s))",
            )
            self.assertEqual(tools["pi_read"]("nested/file.txt"), "hello\nagent")

    def test_file_tools_reject_paths_outside_workspace(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            tools = {tool.__name__: tool for tool in create_pi_tools(temporary_directory)}

            self.assertIn("Path escapes workspace", tools["pi_read"]("../outside.txt"))
            self.assertIn("Path escapes workspace", tools["pi_write"]("../outside.txt", "nope"))

    def test_edit_requires_a_unique_non_empty_match(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tools = {tool.__name__: tool for tool in create_pi_tools(root)}
            tools["pi_write"]("file.txt", "same\nsame")

            ambiguous = tools["pi_edit"]("file.txt", "same", "changed")
            self.assertIn("matched 2 times", ambiguous)
            self.assertEqual(tools["pi_edit"]("file.txt", "", "changed"), "Error: old_text must not be empty")
            self.assertEqual(tools["pi_read"]("file.txt"), "same\nsame")

    def test_bash_runs_in_the_requested_workspace_and_reports_failures(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tools = {tool.__name__: tool for tool in create_pi_tools(root)}
            executable = str(Path(sys.executable).resolve()).replace("\\", "/")
            command = f'"{executable}" -c "import pathlib; print(pathlib.Path.cwd())"'

            output = tools["pi_bash"](command)
            self.assertIn(str(root), output)
            self.assertEqual(tools["pi_bash"]("not-a-real-command"), "Error: Command not found: not-a-real-command")
            self.assertIn("Could not parse command", tools["pi_bash"]('"'))
            self.assertIn("timeout must be positive", tools["pi_bash"]("echo hello", timeout=0))

    def test_workspace_must_be_an_existing_directory(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            file_path = Path(temporary_directory) / "file.txt"
            file_path.write_text("x", encoding="utf-8")

            with self.assertRaises(ValueError):
                resolve_workspace(file_path)
