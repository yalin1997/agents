-- Rebuild collaboration-only CHECK constraints, preserving all immutable data.
-- migrate_reviewed disables foreign keys BEFORE BEGIN only for this registered
-- rebuild, checks all foreign keys before COMMIT, and restores enforcement.
DROP TRIGGER collab_current_policy_guard;
CREATE TEMP TABLE collab_saved_approval_sequence AS
SELECT COALESCE(MAX(seq),0) value FROM sqlite_sequence WHERE name='collab_approval_requests';
CREATE TABLE collab_new_policies (
    run_id TEXT NOT NULL REFERENCES collab_runs(id),
    version INTEGER NOT NULL CHECK(version > 0),
    payload TEXT NOT NULL,
    actor TEXT NOT NULL CHECK(actor='admin'),
    source TEXT NOT NULL CHECK(source IN ('initial','migration','budget_extension','capability','responsibility_exception','validation_plan_change','criteria_change')),
    approval_id TEXT UNIQUE,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id,version),
    FOREIGN KEY(approval_id,run_id) REFERENCES collab_approval_requests(id,run_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE collab_new_approval_requests (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('budget_extension','capability','responsibility_exception','validation_plan_change','criteria_change')),
    policy_version INTEGER NOT NULL,
    target_type TEXT NOT NULL CHECK(target_type IN ('team_run','work_member','repair_root','validation_plan','work_item')),
    old_json TEXT NOT NULL,
    new_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    work_id TEXT,
    created_by TEXT NOT NULL CHECK(created_by='admin'),
    status TEXT NOT NULL CHECK(status IN ('pending','approved','denied','expired','superseded','cancelled')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    expires_ms INTEGER NOT NULL CHECK(expires_ms >= 0),
    created_at TEXT NOT NULL,
    member_id TEXT,
    target_id TEXT NOT NULL,
    UNIQUE(id,run_id),
    FOREIGN KEY(attempt_id,run_id) REFERENCES collab_run_attempts(id,run_id),
    FOREIGN KEY(run_id,policy_version) REFERENCES collab_policies(run_id,version),
    FOREIGN KEY(work_id,run_id) REFERENCES collab_work_items(id,run_id),
    FOREIGN KEY(member_id,run_id) REFERENCES collab_members(id,run_id),
    CHECK((kind='budget_extension' AND target_type IN ('team_run','repair_root') AND member_id IS NULL)
       OR (kind='capability' AND target_type='team_run' AND member_id IS NULL)
       OR (kind='responsibility_exception' AND target_type='work_member' AND member_id IS NOT NULL AND work_id IS NOT NULL AND target_id=member_id)
       OR (kind='validation_plan_change' AND target_type='validation_plan' AND member_id IS NULL)
       OR (kind='criteria_change' AND target_type IN ('team_run','work_item') AND member_id IS NULL)),
    CHECK(target_type!='team_run' OR target_id=run_id)
);
CREATE TABLE collab_new_approval_decisions (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    actor TEXT NOT NULL CHECK(actor='admin'),
    decision TEXT NOT NULL CHECK(decision IN ('approve','deny')),
    reason TEXT NOT NULL,
    applied_policy_version INTEGER,
    created_at TEXT NOT NULL,
    CHECK((decision='approve' AND applied_policy_version IS NOT NULL) OR (decision='deny' AND applied_policy_version IS NULL)),
    FOREIGN KEY(approval_id,run_id) REFERENCES collab_approval_requests(id,run_id),
    FOREIGN KEY(run_id,applied_policy_version) REFERENCES collab_policies(run_id,version)
);
INSERT INTO collab_new_policies SELECT * FROM collab_policies;
INSERT INTO collab_new_approval_requests(sequence,id,run_id,attempt_id,kind,policy_version,target_type,old_json,new_json,reason,work_id,created_by,status,revision,expires_ms,created_at,member_id,target_id)
SELECT sequence,id,run_id,attempt_id,kind,policy_version,target_type,old_json,new_json,reason,work_id,created_by,status,revision,expires_ms,created_at,NULL,run_id FROM collab_approval_requests;
INSERT INTO collab_new_approval_decisions SELECT * FROM collab_approval_decisions;
DROP TABLE collab_approval_decisions;
DROP TABLE collab_approval_requests;
DROP TABLE collab_policies;
ALTER TABLE collab_new_policies RENAME TO collab_policies;
ALTER TABLE collab_new_approval_requests RENAME TO collab_approval_requests;
ALTER TABLE collab_new_approval_decisions RENAME TO collab_approval_decisions;
UPDATE sqlite_sequence SET seq=MAX(seq,(SELECT value FROM collab_saved_approval_sequence))
WHERE name='collab_approval_requests';
DROP TABLE collab_saved_approval_sequence;
CREATE INDEX collab_approval_page ON collab_approval_requests(run_id,sequence);
CREATE TRIGGER collab_policy_immutable BEFORE UPDATE ON collab_policies
BEGIN SELECT RAISE(ABORT,'policy_version_immutable'); END;
CREATE TRIGGER collab_current_policy_guard BEFORE UPDATE OF policy_json,policy_version ON collab_runs
WHEN NEW.policy_version!=OLD.policy_version+1
  OR NOT EXISTS(SELECT 1 FROM collab_policies WHERE run_id=OLD.id AND version=NEW.policy_version AND payload=NEW.policy_json)
BEGIN SELECT RAISE(ABORT,'policy_version_required'); END;
CREATE TRIGGER collab_approval_identity_immutable
BEFORE UPDATE OF sequence,id,run_id,attempt_id,kind,policy_version,target_type,old_json,new_json,reason,work_id,member_id,target_id,created_by,expires_ms,created_at ON collab_approval_requests
BEGIN SELECT RAISE(ABORT,'approval_identity_immutable'); END;
CREATE TRIGGER collab_approval_terminal_immutable BEFORE UPDATE ON collab_approval_requests
WHEN OLD.status!='pending' OR NEW.status='pending' OR NEW.revision!=OLD.revision+1
BEGIN SELECT RAISE(ABORT,'approval_terminal_immutable'); END;
CREATE TRIGGER collab_approval_decision_immutable BEFORE UPDATE ON collab_approval_decisions
BEGIN SELECT RAISE(ABORT,'approval_decision_immutable'); END;
CREATE TABLE collab_responsibility_versions (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    policy_version INTEGER NOT NULL,
    paths_json TEXT NOT NULL,
    FOREIGN KEY(approval_id,run_id) REFERENCES collab_approval_requests(id,run_id),
    FOREIGN KEY(attempt_id,run_id) REFERENCES collab_run_attempts(id,run_id),
    FOREIGN KEY(work_id,run_id) REFERENCES collab_work_items(id,run_id),
    FOREIGN KEY(member_id,run_id) REFERENCES collab_members(id,run_id),
    FOREIGN KEY(run_id,policy_version) REFERENCES collab_policies(run_id,version),
    UNIQUE(run_id,attempt_id,work_id,member_id,policy_version)
);
CREATE TRIGGER collab_responsibility_immutable BEFORE UPDATE ON collab_responsibility_versions
BEGIN SELECT RAISE(ABORT,'responsibility_version_immutable'); END;
CREATE TRIGGER collab_responsibility_no_delete BEFORE DELETE ON collab_responsibility_versions
WHEN EXISTS(SELECT 1 FROM collab_runs r JOIN collab_teams t ON t.id=r.team_id WHERE r.id=OLD.run_id
    AND (t.status!='archived' OR r.status NOT IN ('completed','failed','stopped')
         OR EXISTS(SELECT 1 FROM collab_activity a WHERE a.holder_id=r.id)))
BEGIN SELECT RAISE(ABORT,'responsibility_version_immutable'); END;
