"""Migrate a real legacy Web database, then reopen it with the legacy service."""
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from pi_deepagent.collaboration.service import TeamService
from pi_deepagent.collaboration.storage import CollaborationStore, migrate_reviewed
from pi_deepagent.web.auth import AuthService, Redactor
from pi_deepagent.web.service import WorkspaceService, WebError


class LegacyMigrationTests(IsolatedAsyncioTestCase):
    async def test_real_legacy_tasks_survive_additive_migration_and_backup_restore(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"; workspace.mkdir()
            data = root / "data"; data.mkdir()
            database = data / "tasks.sqlite"
            redactor = Redactor()
            def unused_runner(history, prompt):
                self.fail("Schema compatibility test must not execute an Agent")
            old = WorkspaceService(database, workspace, unused_runner, redactor)
            await old.startup()
            try:
                original = old.create(str(uuid4()))
            finally:
                await old.shutdown()
            backup = data / "before-collaboration.sqlite"
            migrate_reviewed(database, expected_version=0, backup=backup)
            await old.startup()
            try:
                self.assertEqual(old.get_task(original["id"]), original)
                with CollaborationStore(database) as store:
                    auth = AuthService(data, redactor)
                    auth.initialize("temporary-migration-test-admin-456")
                    token, _ = auth.login("admin", "temporary-migration-test-admin-456", "test")
                    service = TeamService(store, auth, workspace)
                    created = service.create_team(token, str(uuid4()), {
                        "name": "new team", "goal": "coexist with old tasks", "criteria": ["old data intact"],
                        "members": [{"name": "lead", "responsibility": "coordinate", "goal": "ship", "orchestrator": True},
                                    {"name": "worker", "responsibility": "implement", "goal": "repair", "orchestrator": False}]})
                    self.assertEqual(service.get_team(token, created["id"]), created)
                after_backup = old.create(str(uuid4()))
                self.assertEqual(old.get_task(original["id"]), original)
                self.assertEqual(len(old.list_tasks()), 2)
            finally:
                await old.shutdown()
            restored_database = data / "restored.sqlite"
            with closing(sqlite3.connect(backup)) as source, closing(sqlite3.connect(restored_database)) as restored:
                source.backup(restored)
            restored_service = WorkspaceService(restored_database, workspace, unused_runner, redactor)
            await restored_service.startup()
            try:
                self.assertEqual(restored_service.get_task(original["id"]), original)
                with self.assertRaises(WebError) as raised:
                    restored_service.get_task(after_backup["id"])
                self.assertEqual(raised.exception.status, 404)
            finally:
                await restored_service.shutdown()
