"""Bounded snapshots of successful text-file outputs, never arbitrary file serving."""
from __future__ import annotations

from pathlib import Path
import re
from uuid import UUID, uuid4


def written_path(name: str, content: str) -> str | None:
    pattern = {"pi_write": r"Wrote \d+ bytes to (.+)",
               "pi_edit": r"Edited (.+) \(\d+ replacement\(s\)\)"}.get(name)
    match = re.fullmatch(pattern, content) if pattern else None
    return match.group(1) if match else None


class ArtifactStore:
    MAX_BYTES = 1048576
    MAX_FILES = 20

    def __init__(self, directory: Path, workspace: Path, redactor):
        self.directory, self.workspace, self.redactor = directory, workspace, redactor

    def capture(self, task_id: str, run_id: str, path: str) -> dict | None:
        try:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = self.workspace / candidate
            relative = candidate.relative_to(self.workspace)
            # Exclude hidden configuration/credentials and all indirect paths.
            if any(part.startswith('.') or ':' in part for part in relative.parts):
                return None
            for parent in (candidate, *candidate.parents):
                if parent == self.workspace:
                    break
                if parent.is_symlink() or parent.is_junction():
                    return None
            target = candidate.resolve(strict=True)
            if not target.is_relative_to(self.workspace) or not target.is_file():
                return None
            with target.open('rb') as source:
                raw = source.read(self.MAX_BYTES + 1)
            if len(raw) > self.MAX_BYTES:
                return None
            text = self.redactor.clean(raw.decode('utf-8'))
            if '\0' in text:
                return None
            content = text.encode('utf-8')
            artifact_id = str(uuid4())
            folder = self.directory / str(UUID(task_id))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / artifact_id).write_bytes(content)
            return {"id": artifact_id, "name": self.redactor.clean(relative.as_posix()),
                    "size": len(content), "run_id": run_id}
        except (OSError, ValueError, RuntimeError):
            # Failed or unsupported exports must not fail the completed chat turn.
            return None

    def read(self, task_id: str, artifact_id: str) -> bytes:
        path = self.directory / str(UUID(task_id)) / str(UUID(artifact_id))
        return self.redactor.clean(path.read_text(encoding='utf-8')).encode('utf-8')
