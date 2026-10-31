CREATE TABLE collab_criteria_versions (
    run_id TEXT NOT NULL REFERENCES collab_runs(id),
    target_type TEXT NOT NULL CHECK(target_type IN ('team_run','work_item')),
    target_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version>0),
    payload TEXT NOT NULL CHECK(json_valid(payload) AND json_type(payload,'$.goal')='text'
        AND json_type(payload,'$.criteria')='array' AND json_array_length(payload,'$.criteria')>0),
    policy_version INTEGER NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('initial','migration','approval')),
    approval_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id,target_type,target_id,version),
    CHECK(target_type!='team_run' OR target_id=run_id),
    CHECK((source IN ('initial','migration') AND version=1 AND approval_id IS NULL)
       OR (source='approval' AND version>1 AND approval_id IS NOT NULL)),
    FOREIGN KEY(run_id,policy_version) REFERENCES collab_policies(run_id,version) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(approval_id,run_id) REFERENCES collab_approval_requests(id,run_id)
);
INSERT INTO collab_criteria_versions
SELECT id,'team_run',id,1,json_object('goal',json_extract(config_json,'$.spec.goal'),
    'criteria',json_extract(config_json,'$.spec.criteria')),policy_version,'migration',NULL,strftime('%Y-%m-%dT%H:%M:%fZ','now') FROM collab_runs;
INSERT INTO collab_criteria_versions
SELECT w.run_id,'work_item',w.id,1,json_object('goal',w.goal,'criteria',json(w.criteria_json)),
    r.policy_version,'migration',NULL,strftime('%Y-%m-%dT%H:%M:%fZ','now') FROM collab_work_items w JOIN collab_runs r ON r.id=w.run_id;
CREATE TRIGGER collab_initial_run_criteria AFTER INSERT ON collab_runs
BEGIN
    INSERT INTO collab_criteria_versions VALUES (NEW.id,'team_run',NEW.id,1,
        json_object('goal',json_extract(NEW.config_json,'$.spec.goal'),'criteria',json_extract(NEW.config_json,'$.spec.criteria')),
        NEW.policy_version,'initial',NULL,NEW.created_at);
END;
CREATE TRIGGER collab_initial_work_criteria AFTER INSERT ON collab_work_items
BEGIN
    INSERT INTO collab_criteria_versions
    SELECT NEW.run_id,'work_item',NEW.id,1,json_object('goal',NEW.goal,'criteria',json(NEW.criteria_json)),
        policy_version,'initial',NULL,strftime('%Y-%m-%dT%H:%M:%fZ','now') FROM collab_runs WHERE id=NEW.run_id;
END;
CREATE TRIGGER collab_criteria_next_version BEFORE INSERT ON collab_criteria_versions
WHEN NEW.version!=COALESCE((SELECT MAX(version)+1 FROM collab_criteria_versions
    WHERE run_id=NEW.run_id AND target_type=NEW.target_type AND target_id=NEW.target_id),1)
BEGIN SELECT RAISE(ABORT,'criteria_version_required'); END;
CREATE TRIGGER collab_criteria_target BEFORE INSERT ON collab_criteria_versions
WHEN NEW.target_type='work_item' AND NOT EXISTS(SELECT 1 FROM collab_work_items WHERE id=NEW.target_id AND run_id=NEW.run_id)
BEGIN SELECT RAISE(ABORT,'criteria_target_mismatch'); END;
CREATE TRIGGER collab_criteria_approval BEFORE INSERT ON collab_criteria_versions
WHEN NEW.source='approval' AND NOT EXISTS(SELECT 1 FROM collab_approval_requests a
    WHERE a.id=NEW.approval_id AND a.run_id=NEW.run_id AND a.kind='criteria_change'
      AND a.target_type=NEW.target_type AND a.target_id=NEW.target_id AND a.status='pending'
      AND a.policy_version+1=NEW.policy_version)
BEGIN SELECT RAISE(ABORT,'criteria_approval_required'); END;
CREATE TRIGGER collab_criteria_immutable BEFORE UPDATE ON collab_criteria_versions
BEGIN SELECT RAISE(ABORT,'criteria_version_immutable'); END;
CREATE TRIGGER collab_criteria_no_delete BEFORE DELETE ON collab_criteria_versions
WHEN EXISTS(SELECT 1 FROM collab_runs r JOIN collab_teams t ON t.id=r.team_id WHERE r.id=OLD.run_id
    AND (t.status!='archived' OR r.status NOT IN ('completed','failed','stopped')
         OR EXISTS(SELECT 1 FROM collab_activity a WHERE a.holder_id=r.id)))
BEGIN SELECT RAISE(ABORT,'criteria_version_immutable'); END;
