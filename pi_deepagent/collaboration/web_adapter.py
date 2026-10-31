"""Authenticated draft-team HTTP contract over the existing domain service.

The outer WebApp owns cookie/Host/Origin/CSRF/JSON checks. TeamService repeats
session, operation and resource authorization inside its transactions. Opening
this adapter never initializes or migrates a database, or starts a team run.
"""
from __future__ import annotations

from urllib.parse import parse_qsl

from .contracts import CollaborationError, opaque_id, strict_fields
from .service import TeamService
from .storage import CollaborationStore


class TeamHTTPAdapter:
    def __init__(self, database, auth, workspace):
        self.store = CollaborationStore(database)
        try:
            self.teams = TeamService(self.store, auth, workspace)
        except BaseException:
            self.store.close()
            raise

    def close(self):
        self.store.close()

    @staticmethod
    def _query(raw):
        try:
            if len(raw) > 1024:
                raise ValueError
            pairs = parse_qsl(raw.decode("ascii"), keep_blank_values=True, strict_parsing=True, max_num_fields=2)
        except (ValueError, UnicodeError):
            raise CollaborationError(400, "invalid_query", "Invalid pagination query") from None
        result = dict(pairs)
        if len(result) != len(pairs) or set(result)-{"cursor", "limit"}:
            raise CollaborationError(400, "invalid_query", "Pagination fields are unknown or repeated")
        if "limit" in result:
            value = result["limit"]
            if not value.isascii() or not value.isdecimal() or len(value)>3:
                raise CollaborationError(400, "invalid_query", "Pagination limit must be an integer")
            result["limit"] = int(value)
        return result

    def dispatch(self, scope, token, data):
        method, path = scope["method"], scope["path"]
        if path == "/api/teams":
            if method == "GET":
                query = self._query(scope.get("query_string", b""))
                return 200, self.teams.list_teams(token, after_id=query.get("cursor"), limit=query.get("limit", 50))
            if method == "POST":
                if scope.get("query_string"):
                    raise CollaborationError(400, "invalid_query", "Create does not accept a query")
                strict_fields(data, ("request_id", "name", "goal", "criteria", "members"), ("policy",))
                request_id = opaque_id(data["request_id"])
                scope["_audit_request_id"] = request_id
                result = self.teams.create_team(token, request_id, {key:value for key,value in data.items() if key!="request_id"})
                scope["_audit_created_team_id"] = result["id"]
                return 201, result
        else:
            parts = path.split("/")
            if len(parts)==4 and parts[1:3]==["api", "teams"]:
                team_id = opaque_id(parts[3])
                if scope.get("query_string"):
                    raise CollaborationError(400, "invalid_query", "Team detail does not accept a query")
                if method == "GET":
                    return 200, self.teams.get_team(token, team_id)
                if method == "PATCH":
                    # This is the existing complete draft-edit contract. No
                    # read/merge outside the ledger that could change replay.
                    strict_fields(data, ("request_id", "expected_revision", "name", "goal", "criteria", "members"), ("policy",))
                    request_id = opaque_id(data["request_id"])
                    scope["_audit_request_id"] = request_id
                    return 200, self.teams.edit_team(token, team_id, request_id,
                        {key:value for key,value in data.items() if key not in ("request_id", "expected_revision")},
                        expected_revision=data["expected_revision"])
        raise CollaborationError(404, "route_not_found", "This collaboration operation is not available")
