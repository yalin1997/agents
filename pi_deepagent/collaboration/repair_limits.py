"""Approved cumulative repair limits; no generation, dispatch or reconciliation."""
from .contracts import CollaborationError, integer, opaque_id
from .storage import utc_now


def change(old, new):
    if not isinstance(old,dict) or not isinstance(new,dict) or set(old)!={"repair_attempts"} or set(new)!={"repair_attempts"}:
        raise CollaborationError(400,"unsupported_budget_target","Repair extensions select only repair_attempts")
    integer(old["repair_attempts"],minimum=0); integer(new["repair_attempts"],minimum=0)
    if new["repair_attempts"]<=old["repair_attempts"]:
        raise CollaborationError(400,"budget_increase_required","Repair extension must increase the total")


def validate_target(kind, old, root_id):
    root_values = isinstance(old,dict) and set(old)=={"repair_attempts"}
    if root_id is not None and kind!="budget_extension":
        raise CollaborationError(400,"invalid_fields","Only budget extensions accept a repair root")
    if kind=="budget_extension" and root_values!=(root_id is not None):
        raise CollaborationError(400,"unsupported_budget_target","Repair attempts require an exact root; run totals cannot select a root")


def current(db, run_id, root_id, work_id=None):
    root_id = opaque_id(root_id)
    row = db.execute("SELECT * FROM collab_repair_roots WHERE id=? AND run_id=?",(root_id,run_id)).fetchone()
    if row is None:
        raise CollaborationError(403,"repair_root_mismatch","Repair root must belong to this run")
    if work_id is not None and not db.execute("SELECT 1 FROM collab_work_roots WHERE work_id=? AND run_id=? AND root_id=?",(work_id,run_id,root_id)).fetchone():
        raise CollaborationError(403,"repair_root_work_mismatch","Related work does not inherit this repair root")
    history = db.execute("SELECT version,limit_total FROM collab_repair_limit_versions WHERE root_id=? ORDER BY version DESC LIMIT 1",(root_id,)).fetchone()
    if history is None or history["limit_total"]!=row["limit_total"]:
        raise CollaborationError(409,"repair_limit_inconsistent","Repair limit history and current total require reconciliation")
    return dict(row) | {"limit_version":history["version"]}


def append(db, run, root_id, approval_id, new):
    before = current(db,run["id"],root_id)
    total = new["repair_attempts"]
    if total<before["used"]+before["reserved"]:
        raise CollaborationError(409,"repair_extension_insufficient","New total does not cover cumulative usage and reservations")
    db.execute("INSERT INTO collab_repair_limit_versions VALUES (?,?,?,?,?,'approval',?,?)",
               (run["id"],root_id,before["limit_version"]+1,total,run["policy_version"]+1,approval_id,utc_now()))
    changed = db.execute("UPDATE collab_repair_roots SET limit_total=?,revision=revision+1 WHERE id=? AND run_id=? AND revision=? AND limit_total=?",
                         (total,root_id,run["id"],before["revision"],before["limit_total"]))
    if changed.rowcount!=1:
        raise CollaborationError(409,"stale_revision","Repair root changed during limit publication")
    return current(db,run["id"],root_id)
