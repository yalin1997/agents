"""Explicit additive migrations and scoped atomic collaboration requests.

No open/import path applies migrations. Callers must authenticate, derive Scope,
and provide an authorization callback; it runs inside the transaction BEFORE
looking up an idempotent response. External operations must not run in these
transactions: later executors use intent/result journals instead.
"""
from __future__ import annotations

from contextlib import contextmanager, closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from .contracts import CollaborationError, FrozenJSON, integer, opaque_id, required_text


MIGRATIONS = tuple(sorted(Path(__file__).with_name("migrations").glob("[0-9][0-9][0-9]_*.sql")))
SCHEMA_VERSION = len(MIGRATIONS)
# SQLite's documented table-rebuild sequence needs FK enforcement disabled
# before BEGIN. This is scoped to the reviewed migration, never normal writes.
FOREIGN_KEY_REBUILDS = frozenset({7})


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _connection(database):
    db = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=rw", uri=True,
                         isolation_level=None, check_same_thread=False, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _version(db):
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collab_schema_migrations'").fetchone()
    if not exists:
        return 0
    return db.execute("SELECT COALESCE(MAX(version),0) FROM collab_schema_migrations").fetchone()[0]


def _verify_schema(db, *, expected_version=None):
    try:
        rows = db.execute("SELECT version,checksum FROM collab_schema_migrations ORDER BY version").fetchall()
    except sqlite3.DatabaseError:
        raise CollaborationError(503, "schema_required", "Collaboration schema requires explicit migration") from None
    expected_version = SCHEMA_VERSION if expected_version is None else expected_version
    expected = [(index, hashlib.sha256(path.read_bytes()).hexdigest())
                for index, path in enumerate(MIGRATIONS[:expected_version], 1)]
    if [tuple(row) for row in rows] != expected:
        raise CollaborationError(503, "schema_mismatch", "Collaboration schema version or checksum does not match")


def _statements(source):
    # executescript commits an existing transaction; use SQLite's own statement
    # recognizer so triggers containing semicolons remain atomic with migration.
    current = ""
    for line in source.splitlines(keepends=True):
        current += line
        if sqlite3.complete_statement(current):
            yield current
            current = ""
    if current.strip():
        raise ValueError("Incomplete migration statement")


def migrate_reviewed(database: str | Path, *, expected_version: int, backup: Path | None = None,
                     target_version: int | None = None):
    """Apply the reviewed foundation SQL; existing data requires a new backup.

    This function is an administrative building block, not an authorization
    mechanism. Production use requires prior approval and a stopped service.
    Only temporary test databases have been migrated during this implementation.
    """
    integer(expected_version, minimum=0)
    target_version = SCHEMA_VERSION if target_version is None else integer(target_version)
    if not expected_version <= target_version <= SCHEMA_VERSION:
        raise CollaborationError(503, "schema_mismatch", "Unsupported collaboration schema version")
    path = Path(database).resolve(strict=True)
    with closing(_connection(path)) as db:
        version = _version(db)
        if version != expected_version:
            raise CollaborationError(409, "schema_revision_changed", "Schema version changed before migration")
        if version == target_version:
            _verify_schema(db, expected_version=version)
            return
        if version:
            _verify_schema(db, expected_version=version)
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if tables and backup is None:
            raise CollaborationError(409, "backup_required", "Existing database requires an explicit backup")
        if backup is not None:
            target = Path(backup).absolute()
            if target.exists() or target.is_symlink():
                raise CollaborationError(409, "backup_exists", "Preserve the existing backup file")
            with target.open("xb"):
                pass
            with closing(sqlite3.connect(target)) as destination:
                db.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise CollaborationError(503, "backup_failed", "Backup integrity check failed")
        rebuild = bool(FOREIGN_KEY_REBUILDS.intersection(range(version+1,target_version+1)))
        if rebuild:
            db.execute("PRAGMA foreign_keys=OFF")
        db.execute("BEGIN IMMEDIATE")
        try:
            if _version(db) != expected_version:
                raise CollaborationError(409, "schema_revision_changed", "Schema version changed during migration")
            for index in range(version, target_version):
                source = MIGRATIONS[index].read_bytes()
                for statement in _statements(source.decode("utf-8")):
                    db.execute(statement)
                db.execute("INSERT INTO collab_schema_migrations VALUES (?,?,?)",
                           (index + 1, hashlib.sha256(source).hexdigest(), utc_now()))
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise CollaborationError(503,"migration_integrity_failed","Reviewed migration failed relational integrity validation")
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            if rebuild:
                db.execute("PRAGMA foreign_keys=ON")


@dataclass(frozen=True, slots=True)
class Scope:
    """Trusted service scope; never accepted from HTTP/model JSON."""
    owner: str
    workspace: str

    def __post_init__(self):
        required_text(self.owner)
        required_text(self.workspace)


class CollaborationStore:
    def __init__(self, database: str | Path):
        self.database = Path(database).resolve()
        if not self.database.is_file():
            raise CollaborationError(503, "schema_required", "Collaboration schema requires explicit migration")
        self._guard = RLock()
        self._db = _connection(self.database)
        self._closed = False
        try:
            _verify_schema(self._db)
        except BaseException:
            self._db.close()
            self._closed = True
            raise

    @staticmethod
    def initialize_empty(database: str | Path):
        path = Path(database).absolute()
        with path.open("xb"):
            pass
        migrate_reviewed(path, expected_version=0)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        with self._guard:
            if not self._closed:
                self._db.close()
                self._closed = True

    @contextmanager
    def transaction(self, *, write=True):
        with self._guard:
            if self._closed:
                raise CollaborationError(503, "store_closed", "Collaboration storage is closed")
            if self._db.in_transaction:
                raise RuntimeError("Nested collaboration transactions are not permitted")
            self._db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield self._db
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def mutate(self, scope: Scope, request_id: str, action: str, resource_id: str, body,
               *, authorize, operation):
        request_id = opaque_id(request_id)
        required_text(action); required_text(resource_id)
        fingerprint = FrozenJSON({"action": action, "resource_id": resource_id, "body": body}).sha256
        with self.transaction() as db:
            authorize(db)
            prior = db.execute("SELECT * FROM collab_requests WHERE request_id=?", (request_id,)).fetchone()
            if prior is not None:
                self._scope_row(scope, prior)
                if prior["fingerprint"] != fingerprint:
                    raise CollaborationError(409, "request_conflict", "Request ID belongs to a different mutation")
                return json.loads(prior["response"])
            result = FrozenJSON(operation(db))
            db.execute("INSERT INTO collab_requests VALUES (?,?,?,?,?,?,?,?)",
                       (request_id, scope.owner, scope.workspace, action, resource_id, fingerprint, result.json, utc_now()))
            return result.value

    @staticmethod
    def _scope_row(scope, row):
        if row["owner"] != scope.owner or row["workspace"] != scope.workspace:
            raise CollaborationError(403, "forbidden", "Resource access denied")

    def request_result(self, scope: Scope, request_id: str, resource_id: str, *, authorize):
        request_id = opaque_id(request_id)
        with self.transaction(write=False) as db:
            authorize(db)
            row = db.execute("SELECT * FROM collab_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise CollaborationError(404, "request_not_found", "Request was not found in the authorized scope")
            self._scope_row(scope, row)
            if row["resource_id"] != resource_id:
                raise CollaborationError(403, "forbidden", "Request does not belong to the requested resource")
            return json.loads(row["response"])

    @staticmethod
    def event(db, scope: Scope, resource_id: str, kind: str, payload):
        if not db.in_transaction:
            raise RuntimeError("Events must be committed with their state mutation")
        required_text(kind); required_text(resource_id)
        result = db.execute("INSERT INTO collab_events(id,owner,workspace,resource_id,kind,payload,created_at) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (str(uuid4()), scope.owner, scope.workspace, resource_id, kind, FrozenJSON(payload).json, utc_now()))
        return result.lastrowid

    def events(self, scope: Scope, resource_id: str, *, authorize, after_sequence=0, limit=50):
        integer(after_sequence, minimum=0, maximum=2**63 - 1)
        integer(limit, maximum=200)
        with self.transaction(write=False) as db:
            authorize(db)
            rows = db.execute("SELECT sequence,id,kind,payload,created_at FROM collab_events "
                              "WHERE owner=? AND workspace=? AND resource_id=? AND sequence>? "
                              "ORDER BY sequence LIMIT ?",
                              (scope.owner, scope.workspace, resource_id, after_sequence, limit + 1)).fetchall()
            selected = rows[:limit]
            return {"items": [{**dict(row), "payload": json.loads(row["payload"])} for row in selected],
                    "next_cursor": selected[-1]["sequence"] if selected else after_sequence,
                    "has_more": len(rows) > limit}
