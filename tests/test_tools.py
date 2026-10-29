from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pi_deepagent.tools import MAX_READ_CHARS, create_pi_tools, resolve_workspace


class WorkspaceToolsTests(TestCase):
    def test_read_offset_limit_empty_missing_and_truncation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tools = {tool.__name__: tool for tool in create_pi_tools(root)}
            (root / "lines.txt").write_text("one\n二\nthree\nfour", encoding="utf-8")
            read = tools["pi_read"]
            for offset, limit, expected in ((2, 2, "二\nthree"), (0, 1, "one"),
                                            (1, 0, "one"), (9, 1, "(empty file)")):
                with self.subTest(offset=offset, limit=limit):
                    self.assertEqual(read("lines.txt", offset=offset, limit=limit), expected)
            (root / "empty.txt").write_text("", encoding="utf-8")
            self.assertEqual(read("empty.txt"), "(empty file)")
            self.assertEqual(read("missing.txt"), "File not found: missing.txt")
            self.assertEqual(read("."), "Path is a directory: .")
            (root / "long.txt").write_text("字" * (MAX_READ_CHARS + 1), encoding="utf-8")
            self.assertEqual(read("long.txt"), "字" * MAX_READ_CHARS + "\n...[truncated]")

    def test_edit_replace_all_and_missing_matches(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tools = {tool.__name__: tool for tool in create_pi_tools(root)}
            (root / "file.txt").write_text("same same", encoding="utf-8")
            edit = tools["pi_edit"]
            self.assertEqual(edit("file.txt", "same", "same"), "Error: new_text is identical to old_text")
            self.assertEqual(edit("file.txt", "absent", "x"), "old_text was not found")
            self.assertIn("Path escapes workspace", edit("../outside.txt", "a", "b"))
            self.assertEqual(edit("file.txt", "same", "新", replace_all=True), "Edited file.txt (2 replacement(s))")
            self.assertEqual((root / "file.txt").read_text(encoding="utf-8"), "新 新")

    def test_bash_reports_stderr_and_nonzero_exit_code(self) -> None:
        with TemporaryDirectory() as directory:
            bash = create_pi_tools(directory)[3]
            executable = Path(sys.executable).as_posix()
            output = bash(f'"{executable}" -c "import sys; print(123); print(456, file=sys.stderr); sys.exit(7)"')
            self.assertEqual(output, "123\n\n[stderr]\n456\n\n[exit_code] 7")

    def test_bash_timeout_preserves_partial_output(self) -> None:
        with TemporaryDirectory() as directory:
            bash = create_pi_tools(directory)[3]
            executable = Path(sys.executable).as_posix()
            output = bash(f'"{executable}" -c "import time; print(123, flush=True); time.sleep(30)"', timeout=1)
            self.assertIn("123", output)
            self.assertIn("[timeout] command timed out after 1 seconds", output)

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
