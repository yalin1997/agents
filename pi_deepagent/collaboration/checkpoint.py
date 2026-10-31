"""Thread-bound SQLite adapter for LangGraph's public checkpoint contract.

This stores framework data, not the application's run/permission truth. Opening
an existing database never migrates it. Explicit initialization is limited to
an empty database; production migrations remain an independently approved step.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Sequence
import json
from pathlib import Path
import sqlite3
from threading import Event, RLock
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP, BaseCheckpointSaver, ChannelVersions, Checkpoint,
    CheckpointMetadata, CheckpointTuple, get_checkpoint_id, get_checkpoint_metadata,
)


class CheckpointAccessError(PermissionError):
    """The caller attempted to address a different member's thread."""


class CheckpointSchemaRequired(RuntimeError):
    """An explicit, reviewed checkpoint schema initialization is required."""


class CheckpointConflictError(RuntimeError):
    """An immutable checkpoint ID was reused with different content."""


SCHEMA = """
CREATE TABLE collab_checkpoints (
    thread_id TEXT NOT NULL, namespace TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
    parent_id TEXT, data_type TEXT NOT NULL, data BLOB NOT NULL,
    metadata_type TEXT NOT NULL, metadata BLOB NOT NULL,
    PRIMARY KEY(thread_id, namespace, checkpoint_id));
CREATE TABLE collab_checkpoint_blobs (
    thread_id TEXT NOT NULL, namespace TEXT NOT NULL, channel TEXT NOT NULL,
    version TEXT NOT NULL, data_type TEXT NOT NULL, data BLOB NOT NULL,
    PRIMARY KEY(thread_id, namespace, channel, version));
CREATE TABLE collab_checkpoint_writes (
    thread_id TEXT NOT NULL, namespace TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL, write_index INTEGER NOT NULL, task_path TEXT NOT NULL,
    channel TEXT NOT NULL, data_type TEXT NOT NULL, data BLOB NOT NULL,
    PRIMARY KEY(thread_id, namespace, checkpoint_id, task_id, write_index));
"""
TABLES = {"collab_checkpoints", "collab_checkpoint_blobs", "collab_checkpoint_writes"}


class SqliteCheckpointSaver(BaseCheckpointSaver[int]):
    """A saver capability bound by the service to exactly one graph thread.

    No method enumerates another thread, including list(None). Namespace is
    retained for native graph internals. Integer versions and delta history
    use BaseCheckpointSaver's implementation; no pruning severs ancestry.
    """

    def __init__(self, database: str | Path, *, thread_id: str, initialize=False):
        super().__init__()
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("A service-bound thread ID is required")
        self.thread_id = thread_id
        self.database = Path(database).resolve()
        self._guard = RLock()
        self._closed = Event()
        if not initialize and not self.database.is_file():
            raise CheckpointSchemaRequired("Checkpoint schema must be initialized explicitly")
        self._db = sqlite3.connect(self.database, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables and initialize:
                self._db.executescript("BEGIN IMMEDIATE;" + SCHEMA + "COMMIT;")
            elif not TABLES.issubset(tables):
                raise CheckpointSchemaRequired("Existing database requires an approved schema migration")
        except BaseException:
            self._db.close()
            self._closed.set()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        with self._guard:
            if not self._closed.is_set():
                self._db.close()
                self._closed.set()

    def _connection(self):
        if self._closed.is_set():
            raise RuntimeError("Checkpoint saver is closed")
        return self._db

    def _scope(self, config: RunnableConfig) -> str:
        configurable = config.get("configurable", {})
        if configurable.get("thread_id") != self.thread_id:
            raise CheckpointAccessError("Checkpoint thread access denied")
        namespace = configurable.get("checkpoint_ns", "")
        if not isinstance(namespace, str):
            raise ValueError("Checkpoint namespace must be a string")
        return namespace

    def _config(self, namespace, identifier):
        return {"configurable": {"thread_id": self.thread_id, "checkpoint_ns": namespace,
                                 "checkpoint_id": identifier}}

    def _tuple(self, row) -> CheckpointTuple:
        key = (self.thread_id, row["namespace"], row["checkpoint_id"])
        checkpoint = self.serde.loads_typed((row["data_type"], row["data"]))
        values = {}
        db = self._connection()
        for channel, version in checkpoint["channel_versions"].items():
            blob = db.execute(
                "SELECT data_type,data FROM collab_checkpoint_blobs "
                "WHERE thread_id=? AND namespace=? AND channel=? AND version=?",
                (*key[:2], channel, json.dumps(version))).fetchone()
            if blob is not None and blob["data_type"] != "empty":
                values[channel] = self.serde.loads_typed((blob["data_type"], blob["data"]))
        checkpoint["channel_values"] = values
        writes = db.execute(
            "SELECT task_id,channel,data_type,data FROM collab_checkpoint_writes "
            "WHERE thread_id=? AND namespace=? AND checkpoint_id=? "
            "ORDER BY task_path,task_id,write_index", key).fetchall()
        return CheckpointTuple(
            config=self._config(row["namespace"], row["checkpoint_id"]), checkpoint=checkpoint,
            metadata=self.serde.loads_typed((row["metadata_type"], row["metadata"])),
            parent_config=self._config(row["namespace"], row["parent_id"]) if row["parent_id"] else None,
            pending_writes=[(r["task_id"], r["channel"], self.serde.loads_typed((r["data_type"], r["data"])))
                            for r in writes])

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        namespace = self._scope(config)
        with self._guard:
            db = self._connection()
            args = [self.thread_id, namespace]
            where = "thread_id=? AND namespace=?"
            if identifier := get_checkpoint_id(config):
                where += " AND checkpoint_id=?"
                args.append(identifier)
            row = db.execute("SELECT * FROM collab_checkpoints WHERE " + where +
                             " ORDER BY checkpoint_id DESC LIMIT 1", args).fetchone()
            return self._tuple(row) if row is not None else None

    def list(self, config: RunnableConfig | None, *, filter=None, before=None,
             limit=None) -> Iterator[CheckpointTuple]:
        if config is not None:
            self._scope(config)
        if before is not None:
            self._scope(before)
        with self._guard:
            where, args = "thread_id=?", [self.thread_id]
            if config and "checkpoint_ns" in config["configurable"]:
                where += " AND namespace=?"
                args.append(config["configurable"]["checkpoint_ns"])
            if config and (identifier := get_checkpoint_id(config)):
                where += " AND checkpoint_id=?"
                args.append(identifier)
            if before and (identifier := get_checkpoint_id(before)):
                where += " AND checkpoint_id<?"
                args.append(identifier)
            rows = self._connection().execute("SELECT * FROM collab_checkpoints WHERE " + where +
                                               " ORDER BY checkpoint_id DESC,namespace", args).fetchall()
            result = []
            for row in rows:
                if limit is not None and len(result) >= limit:
                    break
                value = self._tuple(row)
                if filter and not all(value.metadata.get(k) == v for k, v in filter.items()):
                    continue
                result.append(value)
        return iter(result)

    def put(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata,
            new_versions: ChannelVersions) -> RunnableConfig:
        namespace = self._scope(config)
        data = dict(checkpoint)
        values = data.pop("channel_values")
        serialized = self.serde.dumps_typed(data)
        meta = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        key = (self.thread_id, namespace, checkpoint["id"])
        with self._guard, self._connection() as db:
            prior = db.execute("SELECT * FROM collab_checkpoints WHERE thread_id=? AND namespace=? AND checkpoint_id=?",
                               key).fetchone()
            content = (config["configurable"].get("checkpoint_id"), *serialized, *meta)
            if prior is not None and tuple(prior)[3:] != content:
                raise CheckpointConflictError("Checkpoint ID already has a different result")
            for channel, version in checkpoint["channel_versions"].items():
                if channel not in values and channel not in new_versions:
                    continue  # Existing version remains stored; native graph may omit unchanged values.
                blob = self.serde.dumps_typed(values[channel]) if channel in values else ("empty", b"")
                blob_key = (self.thread_id, namespace, channel, json.dumps(version))
                stored = db.execute("SELECT data_type,data FROM collab_checkpoint_blobs "
                                    "WHERE thread_id=? AND namespace=? AND channel=? AND version=?", blob_key).fetchone()
                if stored is not None and tuple(stored) != blob:
                    raise CheckpointConflictError("Channel version already has a different result")
                db.execute("INSERT OR IGNORE INTO collab_checkpoint_blobs VALUES (?,?,?,?,?,?)", (*blob_key, *blob))
            db.execute("INSERT OR IGNORE INTO collab_checkpoints VALUES (?,?,?,?,?,?,?,?)", (*key, *content))
        return self._config(namespace, checkpoint["id"])

    def put_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]],
                   task_id: str, task_path: str = "") -> None:
        namespace = self._scope(config)
        key = (self.thread_id, namespace, config["configurable"]["checkpoint_id"])
        with self._guard, self._connection() as db:
            for index, (channel, value) in enumerate(writes):
                index = WRITES_IDX_MAP.get(channel, index)
                # LangGraph reserves negative indices for errors/interrupt/resume;
                # those may update, while normal task results are first-write wins.
                verb = "REPLACE" if index < 0 else "IGNORE"
                db.execute(f"INSERT OR {verb} INTO collab_checkpoint_writes VALUES (?,?,?,?,?,?,?,?,?)",
                           (*key, task_id, index, task_path, channel, *self.serde.dumps_typed(value)))

    def delete_thread(self, thread_id: str) -> None:
        self._scope({"configurable": {"thread_id": thread_id}})
        with self._guard, self._connection() as db:
            for table in sorted(TABLES):
                db.execute(f"DELETE FROM {table} WHERE thread_id=?", (self.thread_id,))

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(self, config, *, filter=None, before=None, limit=None) -> AsyncIterator[CheckpointTuple]:
        values = await asyncio.to_thread(self.list, config, filter=filter, before=before, limit=limit)
        for value in values:
            yield value

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id):
        await asyncio.to_thread(self.delete_thread, thread_id)
