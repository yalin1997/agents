-- Earlier schemas have no repair-reservation ledger. Never invent zero usage
-- for existing repair history whose consumed/reserved state cannot be proved.
-- Administrative reconciliation needs a separately reviewed migration.
CREATE TEMP TABLE collab_work_upgrade_guard (
    value INTEGER CONSTRAINT unaccounted_repair_history CHECK(value=0)
);
INSERT INTO collab_work_upgrade_guard
SELECT CASE WHEN EXISTS(SELECT 1 FROM collab_work_attempts WHERE number>1)
    OR EXISTS(SELECT 1 FROM collab_work_items WHERE repairs_work_item_id IS NOT NULL) THEN 1 ELSE 0 END;
DROP TABLE collab_work_upgrade_guard;
CREATE TABLE collab_work_plan_gates (
    run_id TEXT PRIMARY KEY REFERENCES collab_runs(id),
    initial_open INTEGER NOT NULL CHECK(initial_open IN (0,1))
);
INSERT INTO collab_work_plan_gates
SELECT id,CASE WHEN EXISTS(SELECT 1 FROM collab_work_attempts a WHERE a.run_id=collab_runs.id) THEN 0 ELSE 1 END FROM collab_runs;
CREATE TABLE collab_repair_roots (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    limit_total INTEGER NOT NULL CHECK(limit_total >= 0),
    used INTEGER NOT NULL DEFAULT 0 CHECK(used >= 0),
    reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved >= 0),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    UNIQUE(id,run_id),
    FOREIGN KEY(id,run_id) REFERENCES collab_work_items(id,run_id)
);
INSERT INTO collab_repair_roots(id,run_id,limit_total)
SELECT DISTINCT w.repair_root_id,w.run_id,json_extract(r.policy_json,'$.limits.repair_attempts')
FROM collab_work_items w JOIN collab_runs r ON r.id=w.run_id;
CREATE TABLE collab_work_roots (
    work_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    root_id TEXT NOT NULL,
    PRIMARY KEY(work_id,root_id),
    FOREIGN KEY(work_id,run_id) REFERENCES collab_work_items(id,run_id),
    FOREIGN KEY(root_id,run_id) REFERENCES collab_repair_roots(id,run_id)
);
INSERT INTO collab_work_roots SELECT id,run_id,repair_root_id FROM collab_work_items;
CREATE TABLE collab_work_dependencies (
    work_id TEXT NOT NULL,
    depends_on TEXT NOT NULL,
    run_id TEXT NOT NULL,
    CHECK(work_id!=depends_on),
    PRIMARY KEY(work_id,depends_on),
    FOREIGN KEY(work_id,run_id) REFERENCES collab_work_items(id,run_id),
    FOREIGN KEY(depends_on,run_id) REFERENCES collab_work_items(id,run_id)
);
CREATE TABLE collab_planning_contexts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    binding_json TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('initial','child')),
    parents_json TEXT NOT NULL,
    roots_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    FOREIGN KEY(attempt_id,run_id) REFERENCES collab_run_attempts(id,run_id),
    FOREIGN KEY(member_id,run_id) REFERENCES collab_members(id,run_id)
);
CREATE TABLE collab_work_origins (
    work_id TEXT NOT NULL,
    source_work_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    CHECK(work_id!=source_work_id),
    PRIMARY KEY(work_id,source_work_id),
    FOREIGN KEY(work_id,run_id) REFERENCES collab_work_items(id,run_id),
    FOREIGN KEY(source_work_id,run_id) REFERENCES collab_work_items(id,run_id)
);
CREATE TRIGGER collab_initial_plan_cannot_reopen BEFORE UPDATE ON collab_work_plan_gates
WHEN NEW.run_id!=OLD.run_id OR (OLD.initial_open=0 AND NEW.initial_open!=0)
BEGIN SELECT RAISE(ABORT,'initial_plan_sealed'); END;
CREATE TRIGGER collab_attempt_seals_initial_plan AFTER INSERT ON collab_work_attempts
BEGIN
    UPDATE collab_work_plan_gates SET initial_open=0 WHERE run_id=NEW.run_id;
    UPDATE collab_planning_contexts SET active=0 WHERE run_id=NEW.run_id AND kind='initial' AND active=1;
END;
CREATE TRIGGER collab_initial_work_root AFTER INSERT ON collab_work_items WHEN NEW.id=NEW.repair_root_id
BEGIN
    INSERT INTO collab_repair_roots(id,run_id,limit_total)
        SELECT NEW.id,NEW.run_id,json_extract(policy_json,'$.limits.repair_attempts') FROM collab_runs WHERE id=NEW.run_id;
    INSERT INTO collab_work_roots VALUES (NEW.id,NEW.run_id,NEW.id);
END;
CREATE TRIGGER collab_repair_root_identity_immutable BEFORE UPDATE OF id,run_id ON collab_repair_roots
BEGIN SELECT RAISE(ABORT,'repair_root_identity_immutable'); END;
CREATE TRIGGER collab_repair_root_counters_monotonic BEFORE UPDATE ON collab_repair_roots
WHEN NEW.used<OLD.used OR NEW.limit_total<OLD.limit_total
BEGIN SELECT RAISE(ABORT,'repair_root_cannot_reset'); END;
CREATE TRIGGER collab_work_root_immutable BEFORE UPDATE ON collab_work_roots
BEGIN SELECT RAISE(ABORT,'work_roots_immutable'); END;
CREATE TRIGGER collab_work_root_no_delete BEFORE DELETE ON collab_work_roots
WHEN EXISTS(SELECT 1 FROM collab_runs r JOIN collab_teams t ON t.id=r.team_id WHERE r.id=OLD.run_id
    AND (t.status!='archived' OR r.status NOT IN ('completed','failed','stopped')
         OR EXISTS(SELECT 1 FROM collab_activity a WHERE a.holder_id=r.id)))
BEGIN SELECT RAISE(ABORT,'work_roots_immutable'); END;
CREATE TRIGGER collab_work_origin_immutable BEFORE UPDATE ON collab_work_origins
BEGIN SELECT RAISE(ABORT,'work_origins_immutable'); END;
CREATE TRIGGER collab_work_origin_no_delete BEFORE DELETE ON collab_work_origins
WHEN EXISTS(SELECT 1 FROM collab_runs r JOIN collab_teams t ON t.id=r.team_id WHERE r.id=OLD.run_id
    AND (t.status!='archived' OR r.status NOT IN ('completed','failed','stopped')
         OR EXISTS(SELECT 1 FROM collab_activity a WHERE a.holder_id=r.id)))
BEGIN SELECT RAISE(ABORT,'work_origins_immutable'); END;
CREATE TRIGGER collab_work_goal_immutable BEFORE UPDATE OF goal,criteria_json,required ON collab_work_items
BEGIN SELECT RAISE(ABORT,'work_goal_version_required'); END;
CREATE TRIGGER collab_context_immutable BEFORE UPDATE ON collab_planning_contexts
WHEN NEW.id!=OLD.id OR NEW.run_id!=OLD.run_id OR NEW.attempt_id!=OLD.attempt_id OR NEW.member_id!=OLD.member_id
  OR NEW.binding_json!=OLD.binding_json OR NEW.kind!=OLD.kind OR NEW.parents_json!=OLD.parents_json
  OR NEW.roots_json!=OLD.roots_json OR OLD.active=0
BEGIN SELECT RAISE(ABORT,'planning_context_immutable'); END;
