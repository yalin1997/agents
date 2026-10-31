"""Authenticated draft-team operations over the shared collaboration contracts.

No run/executor is registered here yet. Tokens are authenticated for every call
and again inside mutating transactions, including idempotent replays. UI/model
JSON cannot provide owner, workspace, definition IDs, or authorization facts.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .access import AdminAccess
from .contracts import (
    AgentDefinition, CollaborationError, FrozenJSON, MemberSpec, TeamSpec,
    integer, opaque_id,
)
from .storage import CollaborationStore, Scope, utc_now


class TeamService:
    def __init__(self, store: CollaborationStore, auth, workspace: str | Path, *,
                 allowed_actions=frozenset({"read", "create", "edit"})):
        self.store, self.auth = store, auth
        self.access = AdminAccess(auth, workspace, allowed_actions)
        self.workspace, self.allowed_actions = self.access.workspace, self.access.allowed_actions

    def _principal(self, token, action):
        return self.access.principal(token, action)

    def _clean(self, value):
        if isinstance(value, str):
            return self.auth.redactor.clean(value)
        if isinstance(value, list):
            return [self._clean(item) for item in value]
        if isinstance(value, dict):
            return {key: self._clean(item) for key, item in value.items()}
        return value

    def _input(self, value):
        # Validate original shape first; redaction must not turn forbidden fields
        # into a different accepted schema or trust a model-defined identity.
        TeamSpec.from_dict(value)
        return TeamSpec.from_dict(self._clean(value))

    @staticmethod
    def _team(db, scope, team_id):
        row = db.execute("SELECT * FROM collab_teams WHERE id=?", (team_id,)).fetchone()
        if row is None:
            raise CollaborationError(404, "team_not_found", "Team was not found in the authorized scope")
        CollaborationStore._scope_row(scope, row)
        return row

    def _dto(self, row):
        stored = json.loads(row["config"])
        return {"id": row["id"], "status": row["status"], "revision": row["revision"],
                "config": self._clean(stored["spec"]), "definitions": stored["definitions"],
                "config_hash": FrozenJSON(stored).sha256}

    @staticmethod
    def _insert_definition(db, team_id, definition):
        try:
            db.execute("INSERT INTO collab_agent_definitions VALUES (?,?,?,?)",
                       (definition.id, definition.version, team_id, FrozenJSON(definition.spec.to_dict()).json))
        except sqlite3.IntegrityError:
            raise CollaborationError(409, "definition_conflict", "Definition identity or version already exists") from None

    def create_team(self, token, request_id, value):
        scope = self._principal(token, "create")
        spec = self._input(value)
        def operation(db):
            team_id = str(uuid4())
            definitions = [AgentDefinition.create(member) for member in spec.members]
            stored = FrozenJSON({"spec": spec.to_dict(), "definitions": [
                {"id": definition.id, "version": definition.version} for definition in definitions]})
            db.execute("INSERT INTO collab_teams VALUES (?,?,?,?,?,?,?)",
                       (team_id, scope.owner, scope.workspace, "draft", 0, stored.json, utc_now()))
            for definition in definitions:
                self._insert_definition(db, team_id, definition)
            self.store.event(db, scope, team_id, "team_created", {"revision": 0, "config_hash": stored.sha256})
            return self._dto(self._team(db, scope, team_id))
        return self.store.mutate(scope, request_id, "create_team", "teams", value,
                                 authorize=lambda db: self._principal(token, "create"), operation=operation)

    def get_team(self, token, team_id):
        scope = self._principal(token, "read")
        team_id = opaque_id(team_id)
        with self.store.transaction(write=False) as db:
            self._principal(token, "read")
            return self._dto(self._team(db, scope, team_id))

    def list_teams(self, token, *, after_id=None, limit=50):
        scope = self._principal(token, "read")
        integer(limit, maximum=200)
        cursor = opaque_id(after_id) if after_id is not None else ""
        with self.store.transaction(write=False) as db:
            self._principal(token, "read")
            rows = db.execute("SELECT * FROM collab_teams WHERE owner=? AND workspace=? AND id>? ORDER BY id LIMIT ?",
                              (scope.owner, scope.workspace, cursor, limit + 1)).fetchall()
            selected = rows[:limit]
            return {"items": [self._dto(row) for row in selected],
                    "next_cursor": selected[-1]["id"] if len(rows) > limit else None}

    def edit_team(self, token, team_id, request_id, value, *, expected_revision):
        scope = self._principal(token, "edit")
        team_id = opaque_id(team_id)
        integer(expected_revision, minimum=0)
        spec = self._input(value)
        def authorize(db):
            self._principal(token, "edit")
            self._team(db, scope, team_id)
        def operation(db):
            row = self._team(db, scope, team_id)
            if row["status"] != "draft":
                raise CollaborationError(409, "team_archived", "Archived team definitions are read-only")
            if row["revision"] != expected_revision:
                raise CollaborationError(409, "stale_revision", "Team revision has changed")
            old = json.loads(row["config"])
            definitions = []
            # Draft positions retain definition IDs. Reordering edits those draft
            # definitions; active MemberSnapshots will retain the old versions.
            for index, member in enumerate(spec.members):
                insert_new = index >= len(old["definitions"])
                if index < len(old["definitions"]):
                    previous = old["definitions"][index]
                    stored = db.execute("SELECT payload FROM collab_agent_definitions WHERE id=? AND version=? AND team_id=?",
                                        (previous["id"], previous["version"], team_id)).fetchone()
                    definition = AgentDefinition(previous["id"], previous["version"], MemberSpec.from_dict(json.loads(stored["payload"])))
                    if definition.spec != member:
                        definition = definition.revise(member, expected_version=definition.version)
                        insert_new = True
                else:
                    definition = AgentDefinition.create(member)
                if insert_new:
                    self._insert_definition(db, team_id, definition)
                definitions.append({"id": definition.id, "version": definition.version})
            stored = FrozenJSON({"spec": spec.to_dict(), "definitions": definitions})
            result = db.execute("UPDATE collab_teams SET revision=revision+1,config=?,updated_at=? "
                                "WHERE id=? AND revision=? AND status='draft'",
                                (stored.json, utc_now(), team_id, expected_revision))
            if result.rowcount != 1:
                raise CollaborationError(409, "stale_revision", "Team revision has changed")
            self.store.event(db, scope, team_id, "team_updated",
                             {"revision": expected_revision + 1, "config_hash": stored.sha256})
            return self._dto(self._team(db, scope, team_id))
        return self.store.mutate(scope, request_id, "edit_team", team_id,
                                 {"expected_revision": expected_revision, "config": value},
                                 authorize=authorize, operation=operation)
