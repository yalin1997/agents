"""Durable run/attempt records for the trusted coordinator, not an HTTP runner.

prepare() only records a preauthorized fixture/plan. Production callers must
first satisfy admin authorization, workspace snapshot and sandbox preflight.
This module neither executes nor claims to stop external processes. In
particular interrupted activity fences remain held until external reconciliation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
import sqlite3
import time
from uuid import uuid4

from .contracts import (
    AgentDefinition, CollaborationError, FrozenJSON, MemberSnapshot, MemberSpec,
    PolicySpec, integer, opaque_id,
)
from .storage import CollaborationStore, Scope, utc_now
from . import criteria


def conflict(code, message):
    raise CollaborationError(409, code, message)


@dataclass(frozen=True, slots=True)
class RunBinding:
    """Internal model/message budget actor; not a command/workspace capability."""
    run_id: str
    attempt_id: str
    policy_version: int
    actor: str
    grant_id: str
    member_id: str | None = None
    candidate_id: str | None = None
    plan_hash: str | None = None

    def __post_init__(self):
        for name in ("run_id", "attempt_id", "grant_id"):
            object.__setattr__(self, name, opaque_id(getattr(self, name)))
        integer(self.policy_version)
        if self.actor == "member":
            object.__setattr__(self, "member_id", opaque_id(self.member_id))
            if self.candidate_id is not None or self.plan_hash is not None:
                conflict("invalid_actor", "Member binding cannot impersonate a validation actor")
        elif self.actor in ("integration", "validation"):
            if self.member_id is not None or not isinstance(self.plan_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", self.plan_hash):
                conflict("invalid_actor", "Service binding requires an exact candidate and plan")
            object.__setattr__(self, "candidate_id", opaque_id(self.candidate_id))
        else:
            conflict("invalid_actor", "Unrecognized service execution actor")


class RunRepository:
    def __init__(self, store: CollaborationStore, scope: Scope, *, clock_ms=lambda: time.time_ns() // 1_000_000):
        self.store, self.scope, self.clock_ms = store, scope, clock_ms

    def _run(self, db, run_id):
        row = db.execute("SELECT * FROM collab_runs WHERE id=?", (opaque_id(run_id),)).fetchone()
        if row is None:
            raise CollaborationError(404, "run_not_found", "Run was not found in the authorized scope")
        self.store._scope_row(self.scope, row)
        return row

    def _team(self, db, team_id):
        row = db.execute("SELECT * FROM collab_teams WHERE id=?", (opaque_id(team_id),)).fetchone()
        if row is None:
            raise CollaborationError(404, "team_not_found", "Team was not found in the authorized scope")
        self.store._scope_row(self.scope, row)
        return row

    @staticmethod
    def _slot(db, run_id):
        fence = str(uuid4())
        try:
            db.execute("INSERT INTO collab_activity VALUES (1,'team',?,?)", (run_id, fence))
        except sqlite3.IntegrityError:
            conflict("top_level_busy", "Another top-level activity holds the execution fence")
        return fence

    def prepare(self, team_id, request_id, *, expected_revision):
        integer(expected_revision, minimum=0)
        team_id = opaque_id(team_id)
        def operation(db):
            team = self._team(db, team_id)
            if team["status"] != "draft":
                conflict("team_archived", "Archived team cannot prepare a run")
            if team["revision"] != expected_revision:
                conflict("stale_revision", "Team revision has changed")
            # Compatibility guard while the legacy worker bridge is not yet wired.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
                if db.execute("SELECT 1 FROM tasks WHERE status IN ('running','stopping') LIMIT 1").fetchone():
                    conflict("top_level_busy", "A legacy single-agent activity is still active")
            config = json.loads(team["config"])
            policy = PolicySpec.from_dict(config["spec"]["policy"])
            run_id, attempt_id = str(uuid4()), str(uuid4())
            self._slot(db, run_id)
            db.execute("INSERT INTO collab_runs(id,team_id,owner,workspace,status,config_revision,config_json,policy_json,"
                       "current_attempt_id,created_at) VALUES (?,?,?,?,'queued',?,?,?,?,?)",
                       (run_id, team_id, self.scope.owner, self.scope.workspace, expected_revision,
                        team["config"], FrozenJSON(policy.to_dict()).json, attempt_id, utc_now()))
            db.execute("INSERT INTO collab_run_attempts(id,run_id,number,status) VALUES (?,?,1,'queued')", (attempt_id, run_id))
            db.execute("INSERT INTO collab_work_plan_gates VALUES (?,1)", (run_id,))
            db.execute("INSERT INTO collab_policies VALUES (?,1,?,'admin','initial',NULL,?,?)",
                       (run_id, FrozenJSON(policy.to_dict()).json, "Initial preauthorized run policy", utc_now()))
            for ref in config["definitions"]:
                stored = db.execute("SELECT payload FROM collab_agent_definitions WHERE team_id=? AND id=? AND version=?",
                                    (team_id, ref["id"], ref["version"])).fetchone()
                definition = AgentDefinition(ref["id"], ref["version"], MemberSpec.from_dict(json.loads(stored["payload"])))
                member = MemberSnapshot.capture(run_id, definition, FrozenJSON(policy.to_dict()).sha256)
                db.execute("INSERT INTO collab_members VALUES (?,?,?,?,?,?,?)",
                           (member.id, run_id, team_id, member.definition_id, member.definition_version,
                            FrozenJSON(member.spec.to_dict()).json, member.policy_hash))
            db.executemany("INSERT INTO collab_budget_totals(run_id,metric) VALUES (?,?)",
                           [(run_id, "model_calls"), (run_id, "messages")])
            self.store.event(db, self.scope, run_id, "run_prepared", {"attempt_id": attempt_id, "config_revision": expected_revision})
            return self._detail(db, run_id)
        return self.store.mutate(self.scope, request_id, "prepare_run", team_id, {"expected_revision": expected_revision},
                                 authorize=lambda db: self._team(db, team_id), operation=operation)

    def _detail(self, db, run_id):
        row = self._run(db, run_id)
        attempt = db.execute("SELECT * FROM collab_run_attempts WHERE id=? AND run_id=?",
                             (row["current_attempt_id"], run_id)).fetchone()
        members = db.execute("SELECT id,definition_id,definition_version,spec_json,policy_hash FROM collab_members WHERE run_id=? ORDER BY id",
                             (run_id,)).fetchall()
        budgets = db.execute("SELECT metric,used,reserved FROM collab_budget_totals WHERE run_id=?", (run_id,)).fetchall()
        return {"id": row["id"], "team_id": row["team_id"], "status": row["status"], "revision": row["revision"],
                "config_revision": row["config_revision"], "config": json.loads(row["config_json"]),
                "objective":criteria.baseline(db,run_id),
                "policy_version": row["policy_version"], "policy": json.loads(row["policy_json"]),
                "wall_used_ms": row["wall_used_ms"], "terminal_intent": row["terminal_intent"], "attempt": dict(attempt),
                "members": [{**{key: member[key] for key in member.keys() if key != "spec_json"},
                             "spec": json.loads(member["spec_json"])} for member in members],
                "budgets": {budget["metric"]: {"used": budget["used"], "reserved": budget["reserved"]} for budget in budgets}}

    def get(self, run_id):
        with self.store.transaction(write=False) as db:
            return self._detail(db, run_id)

    def bind_member(self, run_id, member_id):
        with self.store.transaction() as db:
            run = self._run(db, run_id)
            member_id = opaque_id(member_id)
            if not db.execute("SELECT 1 FROM collab_members WHERE id=? AND run_id=?", (member_id, run_id)).fetchone():
                raise CollaborationError(403, "member_mismatch", "Member does not belong to this run")
            binding = RunBinding(run_id, run["current_attempt_id"], run["policy_version"], "member", str(uuid4()), member_id)
            db.execute("INSERT INTO collab_actor_grants(id,run_id,attempt_id,member_id,binding_json) VALUES (?,?,?,?,?)",
                       (binding.grant_id, run_id, binding.attempt_id, member_id, FrozenJSON(asdict(binding)).json))
            return binding

    def authorize_binding(self, db, binding, *, allow_historical_policy=False):
        if not isinstance(binding, RunBinding):
            raise CollaborationError(403, "invalid_binding", "A service-bound actor is required")
        run = self._run(db, binding.run_id)
        grant = db.execute("SELECT binding_json,active FROM collab_actor_grants WHERE id=? AND run_id=?",
                           (binding.grant_id, binding.run_id)).fetchone()
        if grant is None or not grant["active"] or grant["binding_json"] != FrozenJSON(asdict(binding)).json:
            raise CollaborationError(403, "invalid_binding", "Actor grant is invalid, altered or revoked")
        if binding.attempt_id != run["current_attempt_id"]:
            conflict("stale_attempt", "Actor belongs to a previous run attempt")
        if binding.policy_version != run["policy_version"]:
            historical = (allow_historical_policy and binding.policy_version < run["policy_version"] and
                          db.execute("SELECT 1 FROM collab_policies WHERE run_id=? AND version=?",
                                     (run["id"], binding.policy_version)).fetchone())
            if not historical:
                conflict("stale_policy", "Actor belongs to a previous policy version")
        if binding.actor == "member" and not db.execute("SELECT 1 FROM collab_members WHERE id=? AND run_id=?",
                                                       (binding.member_id, run["id"])).fetchone():
            raise CollaborationError(403, "member_mismatch", "Member does not belong to this run")
        return run

    def _account_wall(self, db, run_id):
        run = self._run(db, run_id)
        now = integer(self.clock_ms(), minimum=0, maximum=2**63 - 1)
        attempt = db.execute("SELECT * FROM collab_run_attempts WHERE id=?", (run["current_attempt_id"],)).fetchone()
        if attempt["status"] in ("running", "stopping") and attempt["accounted_ms"] is not None:
            delta = max(0, now - attempt["accounted_ms"])
            db.execute("UPDATE collab_runs SET wall_used_ms=wall_used_ms+? WHERE id=?", (delta, run_id))
            db.execute("UPDATE collab_run_attempts SET accounted_ms=? WHERE id=?",
                       (max(now, attempt["accounted_ms"]), attempt["id"]))
        return self._run(db, run_id)

    def _close_dispatch(self, db, run_id, reason):
        run = self._run(db, run_id)
        if run["status"] in ("queued", "running"):
            db.execute("UPDATE collab_runs SET status='stopping',terminal_intent='failed',revision=revision+1 WHERE id=?", (run_id,))
            db.execute("UPDATE collab_run_attempts SET status='stopping',revision=revision+1 WHERE id=?", (run["current_attempt_id"],))
            self._cancel_decisions(db, run_id, "budget_exhausted")
            self._cancel_approvals(db, run_id, "budget_exhausted")
            self.store.event(db, self.scope, run_id, "budget_exhausted", {"reason": reason})

    def _cancel_decisions(self, db, run_id, reason):
        rows = db.execute("SELECT id FROM collab_decisions WHERE run_id=? AND status='pending'", (run_id,)).fetchall()
        db.execute("UPDATE collab_decisions SET status='cancelled',revision=revision+1 WHERE run_id=? AND status='pending'", (run_id,))
        if rows:
            self.store.event(db, self.scope, run_id, "user_questions_cancelled", {"question_ids": [row[0] for row in rows], "reason": reason})

    def _cancel_approvals(self, db, run_id, reason):
        rows = db.execute("SELECT id FROM collab_approval_requests WHERE run_id=? AND status='pending'", (run_id,)).fetchall()
        db.execute("UPDATE collab_approval_requests SET status='cancelled',revision=revision+1 WHERE run_id=? AND status='pending'", (run_id,))
        if rows:
            self.store.event(db, self.scope, run_id, "approval_requests_cancelled", {"approval_ids": [row[0] for row in rows], "reason": reason})

    def _wall_expired(self, db, run_id):
        run = self._account_wall(db, run_id)
        if run["wall_used_ms"] >= json.loads(run["policy_json"])["limits"]["wall_seconds"] * 1000:
            self._close_dispatch(db, run_id, "wall_time")
            return True
        return False

    def claim_start(self, run_id, request_id, *, expected_revision):
        integer(expected_revision, minimum=0)
        def operation(db):
            run = self._run(db, run_id)
            if run["revision"] != expected_revision or run["status"] != "queued":
                conflict("stale_revision", "Run cannot be claimed from this revision/state")
            now = integer(self.clock_ms(), minimum=0, maximum=2**63 - 1)
            db.execute("UPDATE collab_runs SET status='running',revision=revision+1 WHERE id=? AND revision=?", (run_id, expected_revision))
            db.execute("UPDATE collab_run_attempts SET status='running',revision=revision+1,started_ms=?,accounted_ms=? WHERE id=?",
                       (now, now, run["current_attempt_id"]))
            self.store.event(db, self.scope, run_id, "run_start_claimed", {"attempt_id": run["current_attempt_id"]})
            return self._detail(db, run_id)
        return self.store.mutate(self.scope, request_id, "claim_run_start", run_id, {"expected_revision": expected_revision},
                                 authorize=lambda db: self._run(db, run_id), operation=operation)

    def stop(self, run_id, request_id, *, expected_revision):
        integer(expected_revision, minimum=0)
        def operation(db):
            run = self._account_wall(db, run_id)
            if run["revision"] != expected_revision or run["status"] not in ("queued", "running", "stopping"):
                conflict("stale_revision", "Run cannot be stopped from this revision/state")
            if run["status"] == "queued":
                self._finish(db, run_id, "stopped")
            elif run["status"] != "stopping":
                db.execute("UPDATE collab_runs SET status='stopping',terminal_intent='stopped',revision=revision+1 WHERE id=?", (run_id,))
                db.execute("UPDATE collab_run_attempts SET status='stopping',revision=revision+1 WHERE id=?", (run["current_attempt_id"],))
            self._cancel_decisions(db, run_id, "stop_requested")
            self._cancel_approvals(db, run_id, "stop_requested")
            self.store.event(db, self.scope, run_id, "stop_requested", {})
            return self._detail(db, run_id)
        return self.store.mutate(self.scope, request_id, "stop_run", run_id, {"expected_revision": expected_revision},
                                 authorize=lambda db: self._run(db, run_id), operation=operation)

    def _finish(self, db, run_id, status):
        run = self._account_wall(db, run_id)
        if db.execute("SELECT 1 FROM collab_budget_reservations WHERE run_id=? AND status IN ('reserved','unknown') LIMIT 1", (run_id,)).fetchone():
            conflict("resources_not_drained", "Outstanding operation budgets are not reconciled")
        if db.execute("SELECT 1 FROM collab_member_turns WHERE run_id=? AND (status IN ('queued','running') OR certainty='unknown') LIMIT 1", (run_id,)).fetchone():
            conflict("resources_not_drained", "Member turns are active or their effects remain unknown")
        if self._unknown_work(db,run_id):
            conflict("resources_not_drained", "Work attempts have unresolved operation evidence")
        if db.execute("SELECT 1 FROM collab_repair_roots WHERE run_id=? AND reserved>0",(run_id,)).fetchone():
            conflict("resources_not_drained","Repair reservations are not reconciled")
        # WorkAttempt is a logical workflow, not an execution lease. Only the
        # coordinator calls this after all currently represented resources are
        # drained; future execution/journal barriers must precede this too.
        db.execute("UPDATE collab_work_attempts SET status='cancelled',revision=revision+1,outcome_json=? "
                   "WHERE run_id=? AND status IN ('queued','running','submitted','verifying')",
                   (FrozenJSON({"certainty":"known","failure_class":"cancelled","reason":"run_drained"}).json,run_id))
        db.execute("UPDATE collab_work_items SET status='cancelled',revision=revision+1 WHERE run_id=? "
                   "AND status NOT IN ('completed','failed','cancelled','interrupted')",(run_id,))
        db.execute("UPDATE collab_planning_contexts SET active=0 WHERE run_id=? AND active=1",(run_id,))
        attempt = db.execute("SELECT started_ms,accounted_ms FROM collab_run_attempts WHERE id=?", (run["current_attempt_id"],)).fetchone()
        ended_ms = max(self.clock_ms(), attempt["started_ms"] or 0, attempt["accounted_ms"] or 0)
        db.execute("UPDATE collab_run_attempts SET status=?,ended_ms=?,revision=revision+1 WHERE id=?",
                   (status, ended_ms, run["current_attempt_id"]))
        db.execute("UPDATE collab_runs SET status=?,revision=revision+1 WHERE id=?", (status, run_id))
        # Only a record-only coordinator may use this today. Container/execution
        # lease and journal barriers must be added before exposing live run APIs.
        db.execute("DELETE FROM collab_activity WHERE slot=1 AND holder_kind='team' AND holder_id=?", (run_id,))

    @staticmethod
    def _unknown_work(db, run_id):
        return db.execute("SELECT 1 FROM collab_work_attempts WHERE run_id=? "
                          "AND json_extract(outcome_json,'$.certainty')='unknown' LIMIT 1",(run_id,)).fetchone()

    def finish_stopping(self, run_id, request_id, *, attempt_id):
        attempt_id = opaque_id(attempt_id)
        def operation(db):
            run = self._run(db, run_id)
            if run["current_attempt_id"] != attempt_id:
                conflict("stale_attempt", "Drain result belongs to a previous attempt")
            if run["status"] != "stopping":
                conflict("invalid_state", "Run is not draining")
            self._finish(db, run_id, run["terminal_intent"] or "stopped")
            self.store.event(db, self.scope, run_id, "run_drained", {})
            return self._detail(db, run_id)
        return self.store.mutate(self.scope, request_id, "finish_stopping", run_id, {"attempt_id": attempt_id},
                                 authorize=lambda db: self._run(db, run_id), operation=operation)

    def continue_run(self, run_id, request_id, *, expected_revision):
        integer(expected_revision, minimum=0)
        def operation(db):
            run = self._run(db, run_id)
            if run["revision"] != expected_revision or run["status"] not in ("failed", "stopped", "interrupted"):
                conflict("stale_revision", "Run cannot continue from this revision/state")
            if self._team(db, run["team_id"])["status"] != "draft":
                conflict("team_archived", "Archived team cannot continue")
            if db.execute("SELECT 1 FROM collab_budget_reservations WHERE run_id=? AND status IN ('reserved','unknown')", (run_id,)).fetchone():
                conflict("resources_not_drained", "Unknown operations must be reconciled before continuation")
            if db.execute("SELECT 1 FROM collab_member_turns WHERE run_id=? AND (status IN ('queued','running') OR certainty='unknown')",(run_id,)).fetchone() or self._unknown_work(db,run_id):
                conflict("resources_not_drained", "Unknown turns or work require reconciliation before continuation")
            if db.execute("SELECT 1 FROM collab_work_attempts WHERE run_id=? AND status IN ('queued','running','submitted','verifying')",(run_id,)).fetchone():
                conflict("resources_not_drained", "Previous work attempts must be drained before continuation")
            if db.execute("SELECT 1 FROM collab_repair_roots WHERE run_id=? AND reserved>0",(run_id,)).fetchone():
                conflict("resources_not_drained","Repair reservations must be reconciled before continuation")
            limits = json.loads(run["policy_json"])["limits"]
            totals = db.execute("SELECT * FROM collab_budget_totals WHERE run_id=?", (run_id,)).fetchall()
            if run["wall_used_ms"] >= limits["wall_seconds"] * 1000 or any(row["used"] >= limits[row["metric"]] for row in totals):
                conflict("budget_extension_required", "A new budget extension must precede continuation")
            self._slot(db, run_id)
            self._cancel_decisions(db, run_id, "continuation")
            self._cancel_approvals(db, run_id, "continuation")
            old = db.execute("SELECT number FROM collab_run_attempts WHERE id=?", (run["current_attempt_id"],)).fetchone()
            attempt_id = str(uuid4())
            db.execute("INSERT INTO collab_run_attempts(id,run_id,number,status) VALUES (?,?,?,'queued')", (attempt_id, run_id, old["number"] + 1))
            db.execute("UPDATE collab_runs SET status='queued',current_attempt_id=?,terminal_intent=NULL,revision=revision+1 WHERE id=?",
                       (attempt_id, run_id))
            db.execute("UPDATE collab_planning_contexts SET active=0 WHERE run_id=? AND active=1",(run_id,))
            db.execute("UPDATE collab_work_items SET status='pending',revision=revision+1 WHERE run_id=? AND required=1 "
                       "AND status IN ('failed','cancelled','interrupted')",(run_id,))
            self.store.event(db, self.scope, run_id, "continuation_prepared", {"attempt_id": attempt_id})
            return self._detail(db, run_id)
        return self.store.mutate(self.scope, request_id, "continue_run", run_id, {"expected_revision": expected_revision},
                                 authorize=lambda db: self._run(db, run_id), operation=operation)

    def mark_interrupted(self, run_id, request_id, *, attempt_id):
        attempt_id = opaque_id(attempt_id)
        def operation(db):
            run = self._account_wall(db, run_id)
            if run["current_attempt_id"] != attempt_id:
                conflict("stale_attempt", "Interruption belongs to a previous attempt")
            if run["status"] not in ("queued", "running", "stopping"):
                conflict("invalid_state", "Only nonterminal attempts can be interrupted")
            db.execute("UPDATE collab_budget_reservations SET status='unknown' WHERE run_id=? AND status='reserved'", (run_id,))
            self._cancel_decisions(db, run_id, "interrupted")
            self._cancel_approvals(db, run_id, "interrupted")
            db.execute("UPDATE collab_member_turns SET status='interrupted',certainty='unknown',revision=revision+1 "
                       "WHERE run_id=? AND attempt_id=? AND status IN ('queued','running')", (run_id, attempt_id))
            db.execute("UPDATE collab_work_attempts SET status='interrupted',revision=revision+1,outcome_json=? "
                       "WHERE run_id=? AND run_attempt_id=? AND status IN ('queued','running','submitted','verifying')",
                       (FrozenJSON({"certainty":"unknown","reason":"service_interrupted"}).json,run_id,attempt_id))
            db.execute("UPDATE collab_work_items SET status='interrupted',revision=revision+1 WHERE run_id=? "
                       "AND status NOT IN ('completed','failed','cancelled','interrupted')",(run_id,))
            db.execute("UPDATE collab_planning_contexts SET active=0 WHERE run_id=? AND active=1",(run_id,))
            attempt = db.execute("SELECT started_ms,accounted_ms FROM collab_run_attempts WHERE id=?", (attempt_id,)).fetchone()
            db.execute("UPDATE collab_run_attempts SET status='interrupted',ended_ms=?,revision=revision+1 WHERE id=?",
                       (max(self.clock_ms(), attempt["started_ms"] or 0, attempt["accounted_ms"] or 0), run["current_attempt_id"]))
            db.execute("UPDATE collab_runs SET status='interrupted',revision=revision+1 WHERE id=?", (run_id,))
            self.store.event(db, self.scope, run_id, "run_interrupted", {})
            return self._detail(db, run_id)
        return self.store.mutate(self.scope, request_id, "mark_interrupted", run_id, {"attempt_id": attempt_id},
                                 authorize=lambda db: self._run(db, run_id), operation=operation)
