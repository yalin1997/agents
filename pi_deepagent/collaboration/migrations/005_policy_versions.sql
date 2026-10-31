CREATE TABLE collab_policies (
    run_id TEXT NOT NULL REFERENCES collab_runs(id),
    version INTEGER NOT NULL CHECK(version > 0),
    payload TEXT NOT NULL,
    actor TEXT NOT NULL CHECK(actor='admin'),
    source TEXT NOT NULL CHECK(source IN ('initial','migration','budget_extension')),
    approval_id TEXT UNIQUE,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id,version),
    FOREIGN KEY(approval_id,run_id) REFERENCES collab_approval_requests(id,run_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE collab_approval_requests (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind='budget_extension'),
    policy_version INTEGER NOT NULL,
    target_type TEXT NOT NULL CHECK(target_type='team_run'),
    old_json TEXT NOT NULL,
    new_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    work_id TEXT,
    created_by TEXT NOT NULL CHECK(created_by='admin'),
    status TEXT NOT NULL CHECK(status IN ('pending','approved','denied','expired','superseded','cancelled')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    expires_ms INTEGER NOT NULL CHECK(expires_ms >= 0),
    created_at TEXT NOT NULL,
    UNIQUE(id,run_id),
    FOREIGN KEY(attempt_id,run_id) REFERENCES collab_run_attempts(id,run_id),
    FOREIGN KEY(run_id,policy_version) REFERENCES collab_policies(run_id,version),
    FOREIGN KEY(work_id,run_id) REFERENCES collab_work_items(id,run_id)
);
CREATE TABLE collab_approval_decisions (
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
CREATE INDEX collab_approval_page ON collab_approval_requests(run_id,sequence);
INSERT INTO collab_policies(run_id,version,payload,actor,source,approval_id,reason,created_at)
SELECT id,policy_version,policy_json,'admin','migration',NULL,'Existing policy snapshot preserved by explicit migration',created_at FROM collab_runs;
CREATE TRIGGER collab_policy_immutable BEFORE UPDATE ON collab_policies
BEGIN SELECT RAISE(ABORT,'policy_version_immutable'); END;
CREATE TRIGGER collab_current_policy_guard BEFORE UPDATE OF policy_json,policy_version ON collab_runs
WHEN NEW.policy_version!=OLD.policy_version+1
  OR NOT EXISTS(SELECT 1 FROM collab_policies WHERE run_id=OLD.id AND version=NEW.policy_version AND payload=NEW.policy_json)
BEGIN SELECT RAISE(ABORT,'policy_version_required'); END;
CREATE TRIGGER collab_approval_identity_immutable
BEFORE UPDATE OF sequence,id,run_id,attempt_id,kind,policy_version,target_type,old_json,new_json,reason,work_id,created_by,expires_ms,created_at ON collab_approval_requests
BEGIN SELECT RAISE(ABORT,'approval_identity_immutable'); END;
CREATE TRIGGER collab_approval_terminal_immutable BEFORE UPDATE ON collab_approval_requests
WHEN OLD.status!='pending' OR NEW.status='pending' OR NEW.revision!=OLD.revision+1
BEGIN SELECT RAISE(ABORT,'approval_terminal_immutable'); END;
CREATE TRIGGER collab_approval_decision_immutable BEFORE UPDATE ON collab_approval_decisions
BEGIN SELECT RAISE(ABORT,'approval_decision_immutable'); END;
