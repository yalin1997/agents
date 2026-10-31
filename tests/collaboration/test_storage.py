"""Real SQLite ledger atomicity, scoped replay, and explicit migration tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from uuid import uuid4

from pi_deepagent.collaboration.contracts import CollaborationError, FrozenJSON
from pi_deepagent.collaboration.storage import CollaborationStore, Scope, SCHEMA_VERSION, migrate_reviewed


class StorageTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "tasks.sqlite"
        CollaborationStore.initialize_empty(self.path)
        self.store = CollaborationStore(self.path)
        self.addCleanup(self.store.close)
        self.scope = Scope("admin", "workspace-one")
        self.resource = str(uuid4())

    def create(self, db):
        db.execute("INSERT INTO collab_teams VALUES (?,?,?,?,?,?,?)",
                   (self.resource, "admin", "workspace-one", "draft", 0, '{"goal":"original"}', "now"))
        self.store.event(db, self.scope, self.resource, "team_created", {"revision": 0})
        return {"id": self.resource, "revision": 0}

    def test_mutation_and_event_and_request_commit_once_then_return_original_result(self):
        request = str(uuid4())
        authorization_calls = []
        def authorize(db): authorization_calls.append("checked")
        first = self.store.mutate(self.scope, request, "create_team", self.resource, {"goal": "original"},
                                  authorize=authorize, operation=self.create)
        first["revision"] = 99  # caller cannot change the recorded result
        def must_not_run(db): self.fail("idempotent replay executed a second operation")
        second = self.store.mutate(self.scope, request, "create_team", self.resource, {"goal": "original"},
                                   authorize=authorize, operation=must_not_run)
        self.assertEqual(second, {"id": self.resource, "revision": 0})
        self.assertEqual(authorization_calls, ["checked", "checked"])
        with self.store.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_teams").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_events").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_requests").fetchone()[0], 1)
        for body in ({"goal": "changed"}, {"goal": "original", "extra": True}):
            with self.assertRaises(CollaborationError) as raised:
                self.store.mutate(self.scope, request, "create_team", self.resource, body,
                                  authorize=authorize, operation=must_not_run)
            self.assertEqual((raised.exception.status, raised.exception.code), (409, "request_conflict"))

    def test_authorization_revoked_before_replay_does_not_disclose_prior_result(self):
        request = str(uuid4())
        self.store.mutate(self.scope, request, "create_team", self.resource, {},
                          authorize=lambda db: None, operation=self.create)
        def denied(db): raise CollaborationError(403, "forbidden", "Access denied")
        for scope in (self.scope, Scope("other", "workspace-one"), Scope("admin", "workspace-two")):
            with self.subTest(scope=scope), self.assertRaises(CollaborationError) as raised:
                self.store.mutate(scope, request, "create_team", self.resource, {},
                                  authorize=denied, operation=lambda db: self.fail("unauthorized operation"))
            self.assertEqual(raised.exception.status, 403)
        with self.assertRaises(CollaborationError) as raised:
            self.store.request_result(Scope("admin", "workspace-two"), request, self.resource,
                                      authorize=lambda db: None)
        self.assertEqual(raised.exception.status, 403)

    def test_operation_exception_or_non_json_result_rolls_back_all_effects(self):
        for invalid_result in (False, True):
            request = str(uuid4())
            def broken(db):
                self.create(db)
                if invalid_result: return {"not-json": object()}
                raise RuntimeError("injected after mutation and event")
            with self.assertRaises(CollaborationError if invalid_result else RuntimeError):
                self.store.mutate(self.scope, request, "create_team", self.resource, {},
                                  authorize=lambda db: None, operation=broken)
            with self.store.transaction(write=False) as db:
                for table in ("collab_teams", "collab_events", "collab_requests"):
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0)

    def test_two_connections_same_request_do_not_duplicate_effects(self):
        request = str(uuid4())
        barrier = Barrier(2)
        def create_once(index):
            with CollaborationStore(self.path) as store:
                barrier.wait(timeout=5)
                return store.mutate(self.scope, request, "create_team", self.resource, {"goal": "same"},
                                    authorize=lambda db: None, operation=self.create)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create_once, range(2)))
        self.assertEqual(results, [{"id": self.resource, "revision": 0}] * 2)
        with self.store.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_events").fetchone()[0], 1)

    def test_reopen_reads_original_result_and_events_with_monotonic_cursor(self):
        request = str(uuid4())
        result = self.store.mutate(self.scope, request, "create_team", self.resource, {},
                                   authorize=lambda db: None, operation=self.create)
        self.store.close()
        with CollaborationStore(self.path) as reopened:
            self.assertEqual(reopened.request_result(self.scope, request, self.resource, authorize=lambda db: None), result)
            events = reopened.events(self.scope, self.resource, after_sequence=0, limit=1, authorize=lambda db: None)
            self.assertEqual(len(events["items"]), 1)
            self.assertEqual(events["items"][0]["kind"], "team_created")
            self.assertEqual(events["items"][0]["payload"], {"revision": 0})
            self.assertEqual(reopened.events(self.scope, self.resource, after_sequence=events["next_cursor"],
                                            authorize=lambda db: None)["items"], [])
            self.assertEqual(reopened.events(Scope("other", "workspace-one"), self.resource,
                                            authorize=lambda db: None)["items"], [])

    def test_sql_guards_keep_identity_definitions_events_and_ledger_immutable(self):
        request = str(uuid4())
        self.store.mutate(self.scope, request, "create_team", self.resource, {},
                          authorize=lambda db: None, operation=self.create)
        definition = str(uuid4())
        with self.store.transaction() as db:
            db.execute("INSERT INTO collab_agent_definitions VALUES (?,?,?,?)", (definition, 1, self.resource, "{}"))
        for query in ("UPDATE collab_teams SET owner='other'", "UPDATE collab_agent_definitions SET payload='{}'",
                      "UPDATE collab_requests SET response='{}'", "UPDATE collab_events SET payload='{}'"):
            with self.subTest(query=query), self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as db: db.execute(query)

    def test_missing_schema_is_not_created_by_open_and_migration_requires_backup(self):
        missing = self.path.parent / "missing.sqlite"
        with self.assertRaises(CollaborationError): CollaborationStore(missing)
        self.assertFalse(missing.exists())
        existing = self.path.parent / "old.sqlite"
        with closing(sqlite3.connect(existing)) as db, db:
            db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, payload TEXT)")
            db.execute("INSERT INTO tasks VALUES ('old-task', 'keep-old-data')")
        with self.assertRaises(CollaborationError): migrate_reviewed(existing, expected_version=0)
        with self.assertRaises(CollaborationError) as raised: CollaborationStore(existing)
        self.assertEqual(raised.exception.code, "schema_required")
        with closing(sqlite3.connect(existing)) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("tasks",)])
        backup = self.path.parent / "before-migration.sqlite"
        migrate_reviewed(existing, expected_version=0, backup=backup)
        with CollaborationStore(existing) as migrated, migrated.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT * FROM tasks").fetchall()[0]["payload"], "keep-old-data")
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute("SELECT * FROM tasks").fetchall(), [("old-task", "keep-old-data")])
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("tasks",)])
        with self.assertRaises(CollaborationError): migrate_reviewed(existing, expected_version=0, backup=backup)
        migrate_reviewed(existing, expected_version=SCHEMA_VERSION)  # Explicit no-op; no second migration or backup.

    def test_backup_preservation_future_schema_and_transaction_guards(self):
        old = self.path.parent / "another-old.sqlite"
        with closing(sqlite3.connect(old)) as db:
            db.execute("CREATE TABLE tasks (id TEXT)")
        backup = self.path.parent / "preserve-backup.sqlite"
        backup.write_bytes(b"preserve exact backup bytes")
        with self.assertRaises(CollaborationError) as raised:
            migrate_reviewed(old, expected_version=0, backup=backup)
        self.assertEqual(raised.exception.code, "backup_exists")
        self.assertEqual(backup.read_bytes(), b"preserve exact backup bytes")
        with self.store.transaction() as db:
            with self.assertRaisesRegex(RuntimeError, "Nested"):
                with self.store.transaction(): pass
        with self.assertRaisesRegex(RuntimeError, "committed"):
            self.store.event(self.store._db, self.scope, self.resource, "outside_transaction", {})
        with self.assertRaises(CollaborationError) as raised:
            self.store.request_result(self.scope, str(uuid4()), self.resource, authorize=lambda db: None)
        self.assertEqual(raised.exception.status, 404)
        self.store.close()
        with self.assertRaises(CollaborationError) as raised:
            with self.store.transaction(): pass
        self.assertEqual(raised.exception.code, "store_closed")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM collab_schema_migrations")
            db.execute("INSERT INTO collab_schema_migrations VALUES (99,'future','now')")
        with self.assertRaises(CollaborationError) as raised:
            migrate_reviewed(self.path, expected_version=99)
        self.assertEqual(raised.exception.code, "schema_mismatch")

    def test_schema_failure_rolls_back_and_checksum_drift_blocks_open(self):
        existing = self.path.parent / "conflicting.sqlite"
        with closing(sqlite3.connect(existing)) as db:
            db.execute("CREATE TABLE collab_teams (original TEXT)")
        with self.assertRaises(sqlite3.DatabaseError):
            migrate_reviewed(existing, expected_version=0, backup=self.path.parent / "conflicting-backup.sqlite")
        with closing(sqlite3.connect(existing)) as db:
            names = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        self.assertEqual(names, [("collab_teams",)])
        self.store.close()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE collab_schema_migrations SET checksum='tampered'")
        with self.assertRaises(CollaborationError) as raised: CollaborationStore(self.path)
        self.assertEqual(raised.exception.code, "schema_mismatch")

    def test_version_one_upgrade_preserves_team_ledger_and_original_backup(self):
        old = self.path.parent / "version-one.sqlite"
        with closing(sqlite3.connect(old)):
            pass
        migrate_reviewed(old, expected_version=0, target_version=1)
        team, request = str(uuid4()), str(uuid4())
        with closing(sqlite3.connect(old)) as db, db:
            db.execute("INSERT INTO collab_teams VALUES (?,?,?,?,?,?,?)",
                       (team, "admin", "workspace-one", "draft", 0, "{}", "original-time"))
            db.execute("INSERT INTO collab_requests VALUES (?,?,?,?,?,?,?,?)",
                       (request, "admin", "workspace-one", "original", team, "original-hash", '{"kept":true}', "original-time"))
        with self.assertRaises(CollaborationError) as raised: CollaborationStore(old)
        self.assertEqual(raised.exception.code, "schema_mismatch")
        with self.assertRaises(CollaborationError) as raised: migrate_reviewed(old, expected_version=1)
        self.assertEqual(raised.exception.code, "backup_required")
        backup = self.path.parent / "version-one-backup.sqlite"
        migrate_reviewed(old, expected_version=1, backup=backup)
        with CollaborationStore(old) as store, store.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT config FROM collab_teams WHERE id=?", (team,)).fetchone()[0], "{}")
            self.assertEqual(db.execute("SELECT response FROM collab_requests WHERE request_id=?", (request,)).fetchone()[0], '{"kept":true}')
            self.assertEqual(db.execute("SELECT MAX(version) FROM collab_schema_migrations").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute("SELECT MAX(version) FROM collab_schema_migrations").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT response FROM collab_requests").fetchone()[0], '{"kept":true}')
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='collab_runs'").fetchone())
