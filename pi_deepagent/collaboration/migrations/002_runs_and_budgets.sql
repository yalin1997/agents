CREATE UNIQUE INDEX collab_team_parent_scope ON collab_teams(id,owner,workspace);
CREATE TABLE collab_runs (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    workspace TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','stopping','completed','failed','stopped','interrupted')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    config_revision INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    policy_version INTEGER NOT NULL DEFAULT 1 CHECK(policy_version > 0),
    current_attempt_id TEXT NOT NULL,
    wall_used_ms INTEGER NOT NULL DEFAULT 0 CHECK(wall_used_ms >= 0),
    terminal_intent TEXT CHECK(terminal_intent IN ('stopped','failed')),
    created_at TEXT NOT NULL,
    FOREIGN KEY(team_id,owner,workspace) REFERENCES collab_teams(id,owner,workspace),
    FOREIGN KEY(current_attempt_id,id) REFERENCES collab_run_attempts(id,run_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE UNIQUE INDEX collab_run_team_parent ON collab_runs(id,team_id);
CREATE INDEX collab_runs_scope ON collab_runs(owner,workspace,team_id,id);
CREATE TABLE collab_run_attempts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES collab_runs(id) ON DELETE RESTRICT,
    number INTEGER NOT NULL CHECK(number > 0),
    status TEXT NOT NULL CHECK(status IN ('queued','running','stopping','completed','failed','stopped','interrupted')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    started_ms INTEGER,
    ended_ms INTEGER,
    accounted_ms INTEGER,
    UNIQUE(id,run_id),
    UNIQUE(run_id,number)
);
CREATE TABLE collab_members (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES collab_runs(id) ON DELETE RESTRICT,
    team_id TEXT NOT NULL,
    definition_id TEXT NOT NULL,
    definition_version INTEGER NOT NULL,
    spec_json TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    UNIQUE(id,run_id),
    UNIQUE(run_id,definition_id),
    FOREIGN KEY(run_id,team_id) REFERENCES collab_runs(id,team_id),
    FOREIGN KEY(team_id,definition_id,definition_version)
        REFERENCES collab_agent_definitions(team_id,id,version)
);
CREATE TABLE collab_activity (
    slot INTEGER PRIMARY KEY CHECK(slot=1),
    holder_kind TEXT NOT NULL CHECK(holder_kind IN ('single','team')),
    holder_id TEXT NOT NULL,
    fence_id TEXT NOT NULL UNIQUE
);
CREATE TABLE collab_actor_grants (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    member_id TEXT,
    binding_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    FOREIGN KEY(attempt_id,run_id) REFERENCES collab_run_attempts(id,run_id),
    FOREIGN KEY(member_id,run_id) REFERENCES collab_members(id,run_id)
);
CREATE TABLE collab_budget_totals (
    run_id TEXT NOT NULL REFERENCES collab_runs(id) ON DELETE RESTRICT,
    metric TEXT NOT NULL CHECK(metric IN ('model_calls','messages')),
    used INTEGER NOT NULL DEFAULT 0 CHECK(used >= 0),
    reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved >= 0),
    PRIMARY KEY(run_id,metric)
);
CREATE TABLE collab_budget_reservations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    request_id TEXT NOT NULL UNIQUE,
    metric TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount > 0),
    actor TEXT NOT NULL CHECK(actor IN ('member','integration','validation')),
    member_id TEXT,
    binding_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('reserved','consumed','released','unknown')),
    CHECK((actor='member' AND member_id IS NOT NULL) OR (actor!='member' AND member_id IS NULL)),
    FOREIGN KEY(run_id,metric) REFERENCES collab_budget_totals(run_id,metric),
    FOREIGN KEY(attempt_id,run_id) REFERENCES collab_run_attempts(id,run_id),
    FOREIGN KEY(member_id,run_id) REFERENCES collab_members(id,run_id),
    FOREIGN KEY(request_id) REFERENCES collab_requests(request_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TRIGGER collab_run_snapshot_immutable
BEFORE UPDATE OF id,team_id,owner,workspace,config_revision,config_json ON collab_runs
BEGIN SELECT RAISE(ABORT, 'run_snapshot_immutable'); END;
CREATE TRIGGER collab_member_snapshot_immutable BEFORE UPDATE ON collab_members
BEGIN SELECT RAISE(ABORT, 'member_snapshot_immutable'); END;
CREATE TRIGGER collab_attempt_terminal_immutable BEFORE UPDATE ON collab_run_attempts
WHEN OLD.status IN ('completed','failed','stopped','interrupted')
BEGIN SELECT RAISE(ABORT, 'attempt_terminal_immutable'); END;
CREATE TRIGGER collab_budget_usage_monotonic BEFORE UPDATE OF used ON collab_budget_totals
WHEN NEW.used < OLD.used
BEGIN SELECT RAISE(ABORT, 'budget_usage_cannot_decrease'); END;
CREATE TRIGGER collab_wall_usage_monotonic BEFORE UPDATE OF wall_used_ms ON collab_runs
WHEN NEW.wall_used_ms < OLD.wall_used_ms
BEGIN SELECT RAISE(ABORT, 'wall_usage_cannot_decrease'); END;
CREATE TRIGGER collab_reservation_terminal_immutable BEFORE UPDATE ON collab_budget_reservations
WHEN OLD.status IN ('consumed','released')
BEGIN SELECT RAISE(ABORT, 'reservation_terminal_immutable'); END;
CREATE TRIGGER collab_reservation_identity_immutable
BEFORE UPDATE OF id,run_id,attempt_id,request_id,metric,amount,actor,member_id,binding_json ON collab_budget_reservations
BEGIN SELECT RAISE(ABORT, 'reservation_identity_immutable'); END;
CREATE TRIGGER collab_budget_identity_immutable BEFORE UPDATE OF run_id,metric ON collab_budget_totals
BEGIN SELECT RAISE(ABORT, 'budget_identity_immutable'); END;
CREATE TRIGGER collab_attempt_identity_immutable BEFORE UPDATE OF id,run_id,number ON collab_run_attempts
BEGIN SELECT RAISE(ABORT, 'attempt_identity_immutable'); END;
CREATE TRIGGER collab_grant_identity_immutable BEFORE UPDATE ON collab_actor_grants
WHEN NEW.id!=OLD.id OR NEW.run_id!=OLD.run_id OR NEW.attempt_id!=OLD.attempt_id
     OR NEW.member_id IS NOT OLD.member_id OR NEW.binding_json!=OLD.binding_json OR OLD.active=0
BEGIN SELECT RAISE(ABORT, 'grant_identity_immutable'); END;
