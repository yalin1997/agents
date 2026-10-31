"""Draft service authorization with real AuthService, SQLite, and CAS races."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch
from uuid import UUID, uuid4

from pi_deepagent.collaboration.contracts import CollaborationError, MemberSpec
from pi_deepagent.collaboration.service import TeamService
from pi_deepagent.collaboration.storage import CollaborationStore
from pi_deepagent.web.auth import AuthError, AuthService, Redactor


def team(name="team"):
    return {"name": name, "goal": "ship product", "criteria": ["required tests pass"], "members": [
        {"name": "lead", "responsibility": "coordinate", "goal": "integrate", "orchestrator": True},
        {"name": "worker", "responsibility": "implement", "goal": "repair", "orchestrator": False}]}


class TeamServiceTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.workspace = root / "workspace"; self.workspace.mkdir()
        self.data = root / "data"; self.data.mkdir()
        self.path = self.data / "tasks.sqlite"
        CollaborationStore.initialize_empty(self.path)
        self.store = CollaborationStore(self.path); self.addCleanup(self.store.close)
        self.now = 1000.0
        self.auth = AuthService(self.data, Redactor(), clock=lambda: self.now)
        self.auth.initialize("test-admin-password-only-123")
        self.token, _ = self.auth.login("admin", "test-admin-password-only-123", "test")
        self.service = TeamService(self.store, self.auth, self.workspace)

    def test_create_edit_reopen_preserve_server_ids_versions_and_original_request_result(self):
        request = str(uuid4())
        created = self.service.create_team(self.token, request, team())
        self.assertEqual(len(created["definitions"]), 2)
        self.assertNotEqual(created["definitions"][0]["id"], created["definitions"][1]["id"])
        changed = team("edited"); changed["members"][1]["goal"] = "new draft goal"
        edited = self.service.edit_team(self.token, created["id"], str(uuid4()), changed, expected_revision=0)
        self.assertEqual((created["revision"], edited["revision"]), (0, 1))
        self.assertEqual(created["definitions"][1]["id"], edited["definitions"][1]["id"])
        self.assertEqual((created["definitions"][1]["version"], edited["definitions"][1]["version"]), (1, 2))
        self.assertEqual(self.service.create_team(self.token, request, team()), created)
        with CollaborationStore(self.path) as reopened:
            service = TeamService(reopened, self.auth, self.workspace)
            self.assertEqual(service.get_team(self.token, created["id"]), edited)
            self.assertEqual(service.list_teams(self.token)["items"], [edited])
            with reopened.transaction(write=False) as db:
                old = db.execute("SELECT payload FROM collab_agent_definitions WHERE id=? AND version=1",
                                 (created["definitions"][1]["id"],)).fetchone()
                self.assertIn('"goal":"repair"', old["payload"])

    def test_every_operation_rejects_anonymous_nonadmin_logout_and_expired_sessions(self):
        created = self.service.create_team(self.token, str(uuid4()), team())
        def operations(token):
            return [lambda: self.service.get_team(token, created["id"]),
                    lambda: self.service.list_teams(token),
                    lambda: self.service.create_team(token, str(uuid4()), team()),
                    lambda: self.service.edit_team(token, created["id"], str(uuid4()), team(), expected_revision=0)]
        for operation in operations(""):
            with self.assertRaises(AuthError) as raised: operation()
            self.assertEqual(raised.exception.status, 401)
        key = self.auth._digest(self.token)
        original = self.auth.sessions[key]
        self.auth.sessions[key] = replace(original, role="reader")
        for operation in operations(self.token):
            with self.assertRaises(CollaborationError) as raised: operation()
            self.assertEqual(raised.exception.status, 403)
        self.auth.sessions[key] = original
        self.auth.logout(self.token)
        for operation in operations(self.token):
            with self.assertRaises(AuthError): operation()
        token, _ = self.auth.login("admin", "test-admin-password-only-123", "test")
        self.now += 8 * 3600
        for operation in operations(token):
            with self.assertRaises(AuthError): operation()

    def test_workspace_membership_and_operation_permission_precede_dedupe(self):
        request = str(uuid4())
        created = self.service.create_team(self.token, request, team())
        other = self.workspace.parent / "other"; other.mkdir()
        foreign = TeamService(self.store, self.auth, other)
        for operation in (lambda: foreign.get_team(self.token, created["id"]),
                          lambda: foreign.edit_team(self.token, created["id"], str(uuid4()), team(), expected_revision=0),
                          lambda: foreign.create_team(self.token, request, team())):
            with self.assertRaises(CollaborationError) as raised: operation()
            self.assertEqual(raised.exception.status, 403)
        restricted = TeamService(self.store, self.auth, self.workspace, allowed_actions={"read"})
        with self.assertRaises(CollaborationError) as raised:
            restricted.create_team(self.token, request, team())
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(restricted.get_team(self.token, created["id"]), created)

    def test_parallel_edits_with_same_revision_have_one_winner_and_one_event(self):
        created = self.service.create_team(self.token, str(uuid4()), team())
        barrier = Barrier(2)
        def edit(index):
            with CollaborationStore(self.path) as store:
                service = TeamService(store, self.auth, self.workspace)
                barrier.wait(timeout=5)
                try:
                    return service.edit_team(self.token, created["id"], str(uuid4()), team(str(index)), expected_revision=0)
                except CollaborationError as error:
                    return {"error": error.code, "status": error.status}
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(edit, (1, 2)))
        self.assertEqual(sum(r.get("revision") == 1 for r in results), 1)
        self.assertEqual([r for r in results if "error" in r], [{"error": "stale_revision", "status": 409}])
        with self.store.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_events WHERE kind='team_updated'").fetchone()[0], 1)

    def test_secrets_are_redacted_before_db_event_and_ledger_persistence(self):
        secret = "SEEDED-M1-SECRET-not-a-real-token"
        self.auth.redactor.add(secret)
        value = team(secret); value["members"][1]["instructions"] = "avoid echo " + secret
        result = self.service.create_team(self.token, str(uuid4()), value)
        self.assertEqual(result["config"]["name"], "[REDACTED]")
        self.assertNotIn(secret, str(self.service.get_team(self.token, result["id"])))
        with self.store.transaction(write=False) as db:
            dump = "\n".join(db.iterdump())
        self.assertNotIn(secret, dump)
        self.assertIn("team_created", dump)

    def test_pagination_missing_resources_archived_edits_and_invalid_input(self):
        for name in ("one", "two", "three"): self.service.create_team(self.token, str(uuid4()), team(name))
        first = self.service.list_teams(self.token, limit=2)
        second = self.service.list_teams(self.token, after_id=first["next_cursor"], limit=2)
        self.assertEqual(len(first["items"]) + len(second["items"]), 3)
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(len({item["id"] for item in first["items"] + second["items"]}), 3)
        with self.assertRaises(CollaborationError) as raised: self.service.get_team(self.token, str(uuid4()))
        self.assertEqual(raised.exception.status, 404)
        current = first["items"][0]
        with self.store.transaction() as db:
            db.execute("UPDATE collab_teams SET status='archived' WHERE id=?", (current["id"],))
        with self.assertRaises(CollaborationError) as raised:
            self.service.edit_team(self.token, current["id"], str(uuid4()), team(), expected_revision=0)
        self.assertEqual(raised.exception.code, "team_archived")
        bad = team(); bad["owner"] = "attacker"
        with self.assertRaises(CollaborationError): self.service.create_team(self.token, str(uuid4()), bad)

    def test_adding_member_creates_definition_and_suppressed_cas_rolls_back_everything(self):
        created = self.service.create_team(self.token, str(uuid4()), team())
        changed = team(); changed["members"].append({"name": "verifier", "responsibility": "test",
            "goal": "find defects", "orchestrator": False})
        result = self.service.edit_team(self.token, created["id"], str(uuid4()), changed, expected_revision=0)
        self.assertEqual(len(result["definitions"]), 3)
        self.assertEqual(result["definitions"][:2], created["definitions"])
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER test_suppress_cas BEFORE UPDATE ON collab_teams BEGIN SELECT RAISE(IGNORE); END")
            definitions = db.execute("SELECT COUNT(*) FROM collab_agent_definitions").fetchone()[0]
            requests = db.execute("SELECT COUNT(*) FROM collab_requests").fetchone()[0]
            events = db.execute("SELECT COUNT(*) FROM collab_events").fetchone()[0]
        changed["members"][1]["goal"] = "new version must be rolled back"
        with self.assertRaises(CollaborationError) as raised:
            self.service.edit_team(self.token, created["id"], str(uuid4()), changed, expected_revision=1)
        self.assertEqual(raised.exception.code, "stale_revision")
        self.assertEqual(self.service.get_team(self.token, created["id"]), result)
        with self.store.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_agent_definitions").fetchone()[0], definitions)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_requests").fetchone()[0], requests)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_events").fetchone()[0], events)

    def test_new_definition_id_collision_is_rejected_without_rebinding_existing_definition(self):
        created = self.service.create_team(self.token, str(uuid4()), team())
        changed = team(); changed["members"].append({"name": "extra", "responsibility": "test",
            "goal": "new work", "orchestrator": False})
        collision = UUID(created["definitions"][0]["id"])
        with patch("pi_deepagent.collaboration.contracts.uuid4", return_value=collision):
            with self.assertRaises(CollaborationError) as raised:
                self.service.edit_team(self.token, created["id"], str(uuid4()), changed, expected_revision=0)
        self.assertEqual(raised.exception.code, "definition_conflict")
        self.assertEqual(self.service.get_team(self.token, created["id"]), created)
