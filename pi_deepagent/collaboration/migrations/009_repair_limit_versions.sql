CREATE TABLE collab_repair_limit_versions (
    run_id TEXT NOT NULL,
    root_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version>0),
    limit_total INTEGER NOT NULL CHECK(limit_total>=0),
    policy_version INTEGER NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('initial','migration','approval')),
    approval_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY(root_id,version),
    FOREIGN KEY(root_id,run_id) REFERENCES collab_repair_roots(id,run_id),
    FOREIGN KEY(run_id,policy_version) REFERENCES collab_policies(run_id,version),
    FOREIGN KEY(approval_id,run_id) REFERENCES collab_approval_requests(id,run_id),
    CHECK((source IN ('initial','migration') AND version=1 AND approval_id IS NULL)
       OR (source='approval' AND version>1 AND approval_id IS NOT NULL))
);
INSERT INTO collab_repair_limit_versions
SELECT r.run_id,r.id,1,r.limit_total,t.policy_version,'migration',NULL,strftime('%Y-%m-%dT%H:%M:%fZ','now')
FROM collab_repair_roots r JOIN collab_runs t ON t.id=r.run_id;
CREATE TRIGGER collab_initial_repair_limit AFTER INSERT ON collab_repair_roots
BEGIN
    INSERT INTO collab_repair_limit_versions SELECT NEW.run_id,NEW.id,1,NEW.limit_total,policy_version,'initial',NULL,
        strftime('%Y-%m-%dT%H:%M:%fZ','now') FROM collab_runs WHERE id=NEW.run_id;
END;
CREATE TRIGGER collab_repair_limit_next BEFORE INSERT ON collab_repair_limit_versions
WHEN NEW.version!=COALESCE((SELECT MAX(version)+1 FROM collab_repair_limit_versions WHERE root_id=NEW.root_id),1)
BEGIN SELECT RAISE(ABORT,'repair_limit_version_required'); END;
CREATE TRIGGER collab_repair_limit_approval BEFORE INSERT ON collab_repair_limit_versions
WHEN NEW.source='approval' AND NOT EXISTS(
    SELECT 1 FROM collab_approval_requests a JOIN collab_policies p ON p.run_id=a.run_id AND p.version=NEW.policy_version
    WHERE a.id=NEW.approval_id AND a.run_id=NEW.run_id AND a.kind='budget_extension' AND a.target_type='repair_root'
      AND a.target_id=NEW.root_id AND a.status='pending' AND a.policy_version+1=NEW.policy_version
      AND p.approval_id=a.id AND p.source='budget_extension'
      AND json_extract(a.new_json,'$.repair_attempts')=NEW.limit_total)
BEGIN SELECT RAISE(ABORT,'repair_limit_approval_required'); END;
CREATE TRIGGER collab_repair_limit_cache BEFORE UPDATE OF limit_total ON collab_repair_roots
WHEN NEW.limit_total!=OLD.limit_total AND NOT EXISTS(
    SELECT 1 FROM collab_repair_limit_versions v JOIN collab_approval_requests a ON a.id=v.approval_id
    WHERE v.root_id=OLD.id AND v.run_id=OLD.run_id AND v.source='approval' AND v.limit_total=NEW.limit_total
      AND v.version=(SELECT MAX(version) FROM collab_repair_limit_versions WHERE root_id=OLD.id)
      AND a.status='pending' AND json_extract(a.old_json,'$.repair_attempts')=OLD.limit_total)
BEGIN SELECT RAISE(ABORT,'repair_limit_version_required'); END;
CREATE TRIGGER collab_repair_limit_immutable BEFORE UPDATE ON collab_repair_limit_versions
BEGIN SELECT RAISE(ABORT,'repair_limit_version_immutable'); END;
CREATE TRIGGER collab_repair_limit_no_delete BEFORE DELETE ON collab_repair_limit_versions
WHEN EXISTS(SELECT 1 FROM collab_runs r JOIN collab_teams t ON t.id=r.team_id WHERE r.id=OLD.run_id
    AND (t.status!='archived' OR r.status NOT IN ('completed','failed','stopped')
         OR EXISTS(SELECT 1 FROM collab_activity a WHERE a.holder_id=r.id)))
BEGIN SELECT RAISE(ABORT,'repair_limit_version_immutable'); END;
