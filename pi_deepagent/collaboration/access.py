"""Shared admin/session/operation boundary; never derives roles from payloads."""
from pathlib import Path

from .contracts import CollaborationError
from .storage import Scope


class AdminAccess:
    def __init__(self, auth, workspace, allowed_actions):
        self.auth = auth
        self.workspace = str(Path(workspace).resolve(strict=True))
        self.allowed_actions = frozenset(allowed_actions)

    def principal(self, token, action):
        session = self.auth.authenticate(token if isinstance(token, str) else "")
        if session.name != "admin" or session.role != "admin" or action not in self.allowed_actions:
            raise CollaborationError(403, "forbidden", "Operation access denied")
        return Scope(session.name, self.workspace)
