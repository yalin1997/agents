CREATE TABLE collab_work_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES collab_runs(id),
    parent_id TEXT,
    repairs_work_item_id TEXT,
    repair_root_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    criteria_json TEXT NOT NULL,
    required INTEGER NOT NULL CHECK(required IN (0,1)),
    status TEXT NOT NULL CHECK(status IN ('pending','ready','running','waiting_peer','waiting_user','waiting_approval','submitted','verifying','repair_pending','completed','failed','cancelled','interrupted')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    UNIQUE(id,run_id),
    FOREIGN KEY(parent_id,run_id) REFERENCES collab_work_items(id,run_id),
    FOREIGN KEY(repairs_work_item_id,run_id) REFERENCES collab_work_items(id,run_id),
    FOREIGN KEY(repair_root_id,run_id) REFERENCES collab_work_items(id,run_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE collab_work_attempts (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    run_attempt_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    number INTEGER NOT NULL CHECK(number > 0),
    status TEXT NOT NULL CHECK(status IN ('queued','running','submitted','verifying','succeeded','failed','cancelled','interrupted')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    inputs_json TEXT NOT NULL,
    outcome_json TEXT,
    UNIQUE(id,member_id,run_id),
    UNIQUE(work_id,number),
    FOREIGN KEY(work_id,run_id) REFERENCES collab_work_items(id,run_id),
    FOREIGN KEY(run_attempt_id,run_id) REFERENCES collab_run_attempts(id,run_id),
    FOREIGN KEY(member_id,run_id) REFERENCES collab_members(id,run_id)
);
CREATE TABLE collab_result_refs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES collab_runs(id),
    kind TEXT NOT NULL CHECK(kind IN ('change_set','candidate','validation_report')),
    content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    created_at TEXT NOT NULL,
    UNIQUE(id,run_id)
);
CREATE TABLE collab_member_turns (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    work_attempt_id TEXT,
    binding_json TEXT NOT NULL,
    fence_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled','interrupted')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    certainty TEXT CHECK(certainty IN ('known','unknown')),
    created_at TEXT NOT NULL,
    CHECK((status IN ('queued','running') AND certainty IS NULL)
       OR (status='interrupted' AND certainty='unknown')
       OR (status IN ('completed','failed','cancelled') AND certainty='known')),
    UNIQUE(id,member_id,run_id),
    FOREIGN KEY(attempt_id,run_id) REFERENCES collab_run_attempts(id,run_id),
    FOREIGN KEY(member_id,run_id) REFERENCES collab_members(id,run_id),
    FOREIGN KEY(work_attempt_id,member_id,run_id) REFERENCES collab_work_attempts(id,member_id,run_id)
);
CREATE UNIQUE INDEX collab_one_active_member_turn ON collab_member_turns(run_id,member_id)
WHERE status IN ('queued','running');
CREATE TABLE collab_mailbox_cursors (
    run_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    last_sequence INTEGER NOT NULL CHECK(last_sequence >= 0),
    PRIMARY KEY(run_id,member_id),
    FOREIGN KEY(member_id,run_id) REFERENCES collab_members(id,run_id)
);
CREATE TABLE collab_messages (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    recipients_json TEXT NOT NULL,
    work_id TEXT,
    reply_to TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('question','answer','handoff','finding','status')),
    body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 32000),
    artifact_refs_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(id,run_id),
    FOREIGN KEY(attempt_id,run_id) REFERENCES collab_run_attempts(id,run_id),
    FOREIGN KEY(sender_id,run_id) REFERENCES collab_members(id,run_id),
    FOREIGN KEY(work_id,run_id) REFERENCES collab_work_items(id,run_id),
    FOREIGN KEY(reply_to,run_id) REFERENCES collab_messages(id,run_id)
);
CREATE TABLE collab_message_deliveries (
    message_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    status TEXT NOT NULL CHECK(status IN ('accepted','delivered','processed')),
    turn_id TEXT,
    PRIMARY KEY(message_id,recipient_id),
    UNIQUE(run_id,recipient_id,sequence),
    CHECK((status='accepted' AND turn_id IS NULL) OR (status!='accepted' AND turn_id IS NOT NULL)),
    FOREIGN KEY(message_id,run_id) REFERENCES collab_messages(id,run_id),
    FOREIGN KEY(recipient_id,run_id) REFERENCES collab_members(id,run_id),
    FOREIGN KEY(turn_id,recipient_id,run_id) REFERENCES collab_member_turns(id,member_id,run_id)
);
CREATE INDEX collab_delivery_queue ON collab_message_deliveries(run_id,recipient_id,status,sequence);
CREATE TRIGGER collab_work_identity_immutable
BEFORE UPDATE OF id,run_id,parent_id,repairs_work_item_id,repair_root_id ON collab_work_items
BEGIN SELECT RAISE(ABORT, 'work_identity_immutable'); END;
CREATE TRIGGER collab_work_attempt_identity_immutable
BEFORE UPDATE OF id,work_id,run_id,run_attempt_id,member_id,number,inputs_json ON collab_work_attempts
BEGIN SELECT RAISE(ABORT, 'work_attempt_identity_immutable'); END;
CREATE TRIGGER collab_work_attempt_terminal_immutable BEFORE UPDATE ON collab_work_attempts
WHEN OLD.status IN ('succeeded','failed','cancelled','interrupted')
BEGIN SELECT RAISE(ABORT, 'work_attempt_terminal_immutable'); END;
CREATE TRIGGER collab_result_ref_immutable BEFORE UPDATE ON collab_result_refs
BEGIN SELECT RAISE(ABORT, 'result_ref_immutable'); END;
CREATE TRIGGER collab_message_immutable BEFORE UPDATE ON collab_messages
BEGIN SELECT RAISE(ABORT, 'message_immutable'); END;
CREATE TRIGGER collab_turn_identity_immutable
BEFORE UPDATE OF id,run_id,attempt_id,member_id,work_attempt_id,binding_json,fence_id,created_at ON collab_member_turns
BEGIN SELECT RAISE(ABORT, 'turn_identity_immutable'); END;
CREATE TRIGGER collab_turn_terminal_immutable BEFORE UPDATE ON collab_member_turns
WHEN OLD.status IN ('completed','failed','cancelled','interrupted')
BEGIN SELECT RAISE(ABORT, 'turn_terminal_immutable'); END;
CREATE TRIGGER collab_delivery_identity_immutable
BEFORE UPDATE OF message_id,run_id,recipient_id,sequence ON collab_message_deliveries
BEGIN SELECT RAISE(ABORT, 'delivery_identity_immutable'); END;
CREATE TRIGGER collab_delivery_monotonic BEFORE UPDATE ON collab_message_deliveries
WHEN (OLD.status='accepted' AND NEW.status!='delivered')
  OR (OLD.status='delivered' AND (NEW.status!='processed' OR NEW.turn_id!=OLD.turn_id))
  OR OLD.status='processed'
BEGIN SELECT RAISE(ABORT, 'delivery_state_immutable'); END;
CREATE TRIGGER collab_cursor_monotonic BEFORE UPDATE ON collab_mailbox_cursors
WHEN NEW.run_id!=OLD.run_id OR NEW.member_id!=OLD.member_id OR NEW.last_sequence<=OLD.last_sequence
BEGIN SELECT RAISE(ABORT, 'mailbox_cursor_cannot_decrease'); END;
