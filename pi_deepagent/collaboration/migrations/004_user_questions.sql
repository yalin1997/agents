CREATE UNIQUE INDEX collab_work_attempt_context ON collab_work_attempts(id,run_attempt_id,work_id,run_id);
CREATE UNIQUE INDEX collab_one_active_work_attempt ON collab_work_attempts(work_id)
WHERE status IN ('queued','running','submitted','verifying');
CREATE TABLE collab_decisions (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    work_attempt_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind='user_question'),
    policy_version INTEGER NOT NULL CHECK(policy_version > 0),
    work_revision INTEGER NOT NULL CHECK(work_revision >= 0),
    work_attempt_revision INTEGER NOT NULL CHECK(work_attempt_revision >= 0),
    body_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','answered','declined','expired','cancelled','superseded')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    expires_ms INTEGER NOT NULL CHECK(expires_ms >= 0),
    created_at TEXT NOT NULL,
    UNIQUE(id,run_id),
    FOREIGN KEY(attempt_id,run_id) REFERENCES collab_run_attempts(id,run_id),
    FOREIGN KEY(work_attempt_id,attempt_id,work_id,run_id) REFERENCES collab_work_attempts(id,run_attempt_id,work_id,run_id),
    FOREIGN KEY(work_id,run_id) REFERENCES collab_work_items(id,run_id),
    FOREIGN KEY(work_attempt_id,member_id,run_id) REFERENCES collab_work_attempts(id,member_id,run_id)
);
CREATE UNIQUE INDEX collab_one_pending_work_question ON collab_decisions(work_attempt_id)
WHERE status='pending';
CREATE TABLE collab_decision_answers (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    actor TEXT NOT NULL CHECK(actor='admin'),
    outcome TEXT NOT NULL CHECK(outcome IN ('answered','declined')),
    body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 32000),
    created_at TEXT NOT NULL,
    UNIQUE(id,run_id),
    FOREIGN KEY(decision_id,run_id) REFERENCES collab_decisions(id,run_id)
);
CREATE TABLE collab_answer_deliveries (
    answer_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    work_attempt_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    status TEXT NOT NULL CHECK(status IN ('accepted','delivered','processed')),
    turn_id TEXT,
    UNIQUE(run_id,member_id,sequence),
    CHECK((status='accepted' AND turn_id IS NULL) OR (status!='accepted' AND turn_id IS NOT NULL)),
    FOREIGN KEY(answer_id,run_id) REFERENCES collab_decision_answers(id,run_id),
    FOREIGN KEY(attempt_id,run_id) REFERENCES collab_run_attempts(id,run_id),
    FOREIGN KEY(work_attempt_id,member_id,run_id) REFERENCES collab_work_attempts(id,member_id,run_id),
    FOREIGN KEY(turn_id,member_id,run_id) REFERENCES collab_member_turns(id,member_id,run_id)
);
CREATE INDEX collab_decision_page ON collab_decisions(run_id,sequence);
CREATE INDEX collab_answer_queue ON collab_answer_deliveries(run_id,member_id,status,sequence);
CREATE TRIGGER collab_decision_identity_immutable
BEFORE UPDATE OF sequence,id,run_id,attempt_id,member_id,work_id,work_attempt_id,kind,policy_version,work_revision,work_attempt_revision,body_json,expires_ms,created_at ON collab_decisions
BEGIN SELECT RAISE(ABORT,'decision_identity_immutable'); END;
CREATE TRIGGER collab_decision_terminal_immutable BEFORE UPDATE ON collab_decisions
WHEN OLD.status!='pending' OR NEW.status='pending' OR NEW.revision!=OLD.revision+1
BEGIN SELECT RAISE(ABORT,'decision_terminal_immutable'); END;
CREATE TRIGGER collab_answer_immutable BEFORE UPDATE ON collab_decision_answers
BEGIN SELECT RAISE(ABORT,'answer_immutable'); END;
CREATE TRIGGER collab_answer_delivery_identity_immutable
BEFORE UPDATE OF answer_id,run_id,attempt_id,member_id,work_attempt_id,sequence ON collab_answer_deliveries
BEGIN SELECT RAISE(ABORT,'answer_delivery_identity_immutable'); END;
CREATE TRIGGER collab_answer_delivery_monotonic BEFORE UPDATE ON collab_answer_deliveries
WHEN (OLD.status='accepted' AND NEW.status!='delivered')
  OR (OLD.status='delivered' AND (NEW.status!='processed' OR NEW.turn_id!=OLD.turn_id))
  OR OLD.status='processed'
BEGIN SELECT RAISE(ABORT,'answer_delivery_state_immutable'); END;
