CREATE TABLE collab_schema_migrations (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE collab_teams (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    workspace TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft','archived')),
    revision INTEGER NOT NULL CHECK(revision >= 0),
    config TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX collab_teams_scope ON collab_teams(owner, workspace, updated_at, id);
CREATE TABLE collab_agent_definitions (
    id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version > 0),
    team_id TEXT NOT NULL REFERENCES collab_teams(id) ON DELETE RESTRICT,
    payload TEXT NOT NULL,
    PRIMARY KEY(id, version),
    UNIQUE(team_id, id, version)
);
CREATE TABLE collab_requests (
    request_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    workspace TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX collab_requests_scope ON collab_requests(owner, workspace, resource_id);
CREATE TABLE collab_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    owner TEXT NOT NULL,
    workspace TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX collab_events_scope ON collab_events(owner, workspace, resource_id, sequence);
CREATE TRIGGER collab_team_identity_immutable
BEFORE UPDATE OF id,owner,workspace ON collab_teams
WHEN NEW.id != OLD.id OR NEW.owner != OLD.owner OR NEW.workspace != OLD.workspace
BEGIN
    SELECT RAISE(ABORT, 'team_identity_immutable');
END;
CREATE TRIGGER collab_definition_immutable BEFORE UPDATE ON collab_agent_definitions
BEGIN
    SELECT RAISE(ABORT, 'definition_immutable');
END;
CREATE TRIGGER collab_request_immutable BEFORE UPDATE ON collab_requests
BEGIN
    SELECT RAISE(ABORT, 'request_immutable');
END;
CREATE TRIGGER collab_event_immutable BEFORE UPDATE ON collab_events
BEGIN
    SELECT RAISE(ABORT, 'event_immutable');
END;
