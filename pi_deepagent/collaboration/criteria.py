"""Immutable objective documents, read by already-authorized coordinators.

These helpers never authorize execution or turn an old validation into a pass.
Callers must establish admin/resource or service-binding authority first.
"""
from __future__ import annotations

import json
import re

from .contracts import CollaborationError, FrozenJSON, integer, opaque_id, required_text, strict_fields
from .storage import utc_now


def document(value, *, bounded=True):
    strict_fields(value,("goal","criteria"))
    required_text(value["goal"])
    if not isinstance(value["criteria"],list) or not value["criteria"]:
        raise CollaborationError(400,"invalid_criteria","At least one explicit criterion is required")
    for text in value["criteria"]: required_text(text)
    frozen = FrozenJSON(value)
    if bounded and len(frozen.json.encode("utf-8"))>128*1024:
        raise CollaborationError(413,"criteria_too_large","Objective document exceeds the size limit")
    return frozen.value


def reference(value):
    strict_fields(value,("version","hash"),("goal","criteria"))
    integer(value["version"])
    if not isinstance(value["hash"],str) or re.fullmatch(r"[0-9a-f]{64}",value["hash"]) is None:
        raise CollaborationError(400,"invalid_criteria_hash","A canonical objective hash is required")
    if ("goal" in value)!=("criteria" in value):
        raise CollaborationError(400,"invalid_fields","Old objective text must be complete or omitted")
    if "goal" in value:
        old = document({"goal":value["goal"],"criteria":value["criteria"]},bounded=False)
        if FrozenJSON(old).sha256!=value["hash"]:
            raise CollaborationError(400,"invalid_criteria_hash","Old objective text does not match its hash")
    return FrozenJSON(value).value


def target(db, run_id, work_id=None):
    if work_id is None: return "team_run",run_id
    work_id = opaque_id(work_id)
    if not db.execute("SELECT 1 FROM collab_work_items WHERE id=? AND run_id=?",(work_id,run_id)).fetchone():
        raise CollaborationError(403,"work_mismatch","Objective work must belong to this run")
    return "work_item",work_id


def dto(row):
    payload = json.loads(row["payload"])
    return {**{key:row[key] for key in row.keys() if key!="payload"},**payload,"hash":FrozenJSON(payload).sha256}


def current(db, run_id, work_id=None):
    kind,identifier = target(db,run_id,work_id)
    row = db.execute("SELECT * FROM collab_criteria_versions WHERE run_id=? AND target_type=? AND target_id=? ORDER BY version DESC LIMIT 1",
                     (run_id,kind,identifier)).fetchone()
    if row is None:
        raise CollaborationError(409,"criteria_missing","Target has no authoritative objective version")
    return dto(row)


def baseline(db, run_id, work_id=None):
    return {key:value for key,value in current(db,run_id,work_id).items() if key in ("version","hash","goal","criteria")}


def append(db, run, work_id, approval_id, value):
    before = current(db,run["id"],work_id)
    work = None if work_id is None else db.execute("SELECT revision FROM collab_work_items WHERE id=? AND run_id=?",(work_id,run["id"])).fetchone()
    db.execute("INSERT INTO collab_criteria_versions VALUES (?,?,?,?,?,?,'approval',?,?)",
               (run["id"],before["target_type"],before["target_id"],before["version"]+1,FrozenJSON(value).json,
                run["policy_version"]+1,approval_id,utc_now()))
    if work is not None:
        changed = db.execute("UPDATE collab_work_items SET revision=revision+1 WHERE id=? AND run_id=? AND revision=?",
                             (work_id,run["id"],work["revision"]))
        if changed.rowcount!=1:
            raise CollaborationError(409,"stale_revision","Work changed during objective publication")
    return current(db,run["id"],work_id)


def snapshot(db, run_id, work_id):
    return {name:{key:value[key] for key in ("version","hash")} for name,value in (
        ("team_criteria",current(db,run_id)),("work_criteria",current(db,run_id,work_id)))}


def matches(db, run_id, work_id, value):
    """Identity check only; caller still needs actual current-plan gate evidence."""
    if not isinstance(value,dict) or set(value)!={"team_criteria","work_criteria"}:
        return False
    try:
        for item in value.values():
            strict_fields(item,("version","hash")); reference(item)
    except CollaborationError:
        return False
    return snapshot(db,run_id,work_id)==value
