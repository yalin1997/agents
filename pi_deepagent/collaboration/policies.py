"""Immutable run policies and explicit, typed administrator decisions.

This service changes policy ceilings; it does not issue executable capabilities
or prove isolation. Changing policy never drains an operation, resumes a run,
edits an attempt, or bypasses validation/publication gates.
Capability ceilings are available only when a trusted runtime has supplied its
supported set. Responsibility exceptions affect submission rules, never mounts
or filesystem access. Revocation and executable grants remain executor duties.
"""
from __future__ import annotations

import json
from uuid import uuid4

from .access import AdminAccess
from .budgets import BudgetRepository
from .contracts import CollaborationError, FrozenJSON, PolicySpec, integer, opaque_id, relative_path, required_text, strict_fields
from .runs import RunRepository, conflict
from .storage import utc_now
from . import criteria
from . import repair_limits


RUN_LIMITS = frozenset({"model_calls", "messages", "wall_seconds"})
CAPABILITIES = frozenset({"execute","dependency_install","network","publish"})
APPROVAL_KINDS = frozenset({"budget_extension","capability","responsibility_exception","criteria_change"})


class PolicyService:
    def __init__(self, store, auth, workspace, *, clock_ms=None, ttl_seconds=1800,
                 allowed_actions=frozenset({"read", "request", "approve"}), available_capabilities=frozenset()):
        self.store, self.auth = store, auth
        self.access = AdminAccess(auth, workspace, allowed_actions)
        self.clock_ms, self.ttl_ms = clock_ms, integer(ttl_seconds, maximum=86400)*1000
        self.available_capabilities = frozenset(available_capabilities)
        if not self.available_capabilities <= CAPABILITIES:
            raise CollaborationError(400,"invalid_capability_support","Runtime capability support is invalid")

    def _runs(self, token, action):
        scope = self.access.principal(token, action)
        return RunRepository(self.store, scope, **({} if self.clock_ms is None else {"clock_ms": self.clock_ms}))

    def _text(self, value):
        required_text(value)
        result = self.auth.redactor.clean(value)
        if len(value) > 32000 or len(result) > 32000:
            raise CollaborationError(400, "reason_too_long", "Approval reason exceeds the text limit")
        return result

    @staticmethod
    def _change(old, new):
        if not isinstance(old, dict) or not isinstance(new, dict) or not old or old.keys() != new.keys() or not set(old) <= RUN_LIMITS:
            raise CollaborationError(400, "unsupported_budget_target", "Select matching model, message or wall total limits")
        for key in old:
            integer(old[key]); integer(new[key])
        if any(new[key] < old[key] for key in old) or all(new[key] == old[key] for key in old):
            raise CollaborationError(400, "budget_increase_required", "Extension must increase totals without decreasing another limit")

    @staticmethod
    def _approval(db, run_id, approval_id):
        row = db.execute("SELECT * FROM collab_approval_requests WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            raise CollaborationError(404, "approval_not_found", "Approval request was not found")
        if row["run_id"] != run_id:
            raise CollaborationError(403, "approval_mismatch", "Approval does not belong to this run")
        return row

    @staticmethod
    def _covers(prefix, path):
        return prefix=="." or prefix==path or path.startswith(prefix+"/")

    @classmethod
    def _paths(cls, value):
        if not isinstance(value,list) or len(value)>200:
            raise CollaborationError(400,"invalid_responsibility_paths","Responsibility paths must be a bounded array")
        paths = [relative_path(item) for item in value]
        if len(set(paths))!=len(paths):
            raise CollaborationError(400,"duplicate_responsibility_path","Responsibility paths must be unique")
        return sorted(path for path in paths if not any(other!=path and cls._covers(other,path) for other in paths))

    def _typed_change(self, kind, old, new, *, admitting=False):
        if kind=="budget_extension":
            if isinstance(old,dict) and set(old)=={"repair_attempts"}: repair_limits.change(old,new)
            else: self._change(old,new)
        elif kind=="capability":
            if not isinstance(old,dict) or not isinstance(new,dict) or not old or old.keys()!=new.keys() or not set(old)<=CAPABILITIES:
                raise CollaborationError(400,"unsupported_capability_target","Select matching capability ceilings")
            if any(type(value) is not bool for value in [*old.values(),*new.values()]):
                raise CollaborationError(400,"invalid_capability","Capabilities must be explicit booleans")
            if any(old[key] and not new[key] for key in old):
                raise CollaborationError(409,"revocation_required","Capability removal requires the separate revocation protocol")
            if not any(new[key]!=old[key] for key in old):
                raise CollaborationError(400,"capability_increase_required","A capability approval must increase a ceiling")
            if admitting and any(new[key] and not old[key] and key not in self.available_capabilities for key in old):
                raise CollaborationError(503,"capability_unavailable","Runtime has not enabled this isolated capability")
        elif kind=="responsibility_exception":
            strict_fields(old,("paths",)); strict_fields(new,("paths",))
            old,new = {"paths":self._paths(old["paths"])},{"paths":self._paths(new["paths"])}
            if old==new or any(not any(self._covers(prefix,path) for prefix in new["paths"]) for path in old["paths"]):
                raise CollaborationError(400,"responsibility_extension_required","An exception must extend and preserve existing responsibility")
            if admitting and any(self.auth.redactor.clean(path)!=path for path in [*old["paths"],*new["paths"]]):
                raise CollaborationError(400,"sensitive_responsibility_path","Responsibility cannot contain known secrets")
        elif kind=="criteria_change":
            old,new = criteria.reference(old),criteria.document(new)
            if admitting and self._clean_objective(new)!=new:
                conflict("criteria_redaction_changed","Objective text now contains a known secret; create a redacted request")
        else:
            raise CollaborationError(400,"unsupported_approval_kind","Approval kind has no implemented target validator")
        return FrozenJSON(old).value,FrozenJSON(new).value

    def _responsibility(self, db, run, work_id, member_id):
        member = db.execute("SELECT spec_json FROM collab_members WHERE id=? AND run_id=?",(member_id,run["id"])).fetchone()
        if member is None:
            raise CollaborationError(403,"member_mismatch","Responsibility member must belong to this run")
        work = db.execute("SELECT status FROM collab_work_items WHERE id=? AND run_id=?",(work_id,run["id"])).fetchone()
        if work is None:
            raise CollaborationError(403,"work_mismatch","Responsibility work must belong to this run")
        row = db.execute("SELECT paths_json FROM collab_responsibility_versions WHERE run_id=? AND attempt_id=? AND work_id=? AND member_id=? "
                         "AND policy_version<=? ORDER BY policy_version DESC LIMIT 1",
                         (run["id"],run["current_attempt_id"],work_id,member_id,run["policy_version"])).fetchone()
        paths = json.loads(row[0]) if row else self._paths(json.loads(member[0])["paths"])
        return paths,work["status"]

    def _baseline(self, db, run, kind, old, work_id, member_id, *, root_id=None):
        if root_id is not None:
            return {"repair_attempts":repair_limits.current(db,run["id"],root_id,work_id)["limit_total"]}
        policy = json.loads(run["policy_json"])
        if kind=="criteria_change":
            if work_id is not None:
                criteria.target(db,run["id"],work_id)
                work = db.execute("SELECT status FROM collab_work_items WHERE id=? AND run_id=?",(work_id,run["id"])).fetchone()
                if work["status"] in ("completed","failed","cancelled","interrupted"):
                    conflict("work_closed","Terminal work needs explicit continuation or a related follow-up item")
            return criteria.baseline(db,run["id"],work_id)
        if kind=="responsibility_exception":
            paths,status = self._responsibility(db,run,work_id,member_id)
            if status in ("completed","failed","cancelled","interrupted"):
                conflict("work_closed","Responsibility target work is terminal")
            return {"paths":paths}
        source = policy["limits"] if kind=="budget_extension" else policy
        return {key:source[key] for key in old}

    def _dto(self, row):
        result = {key: row[key] for key in ("id", "run_id", "attempt_id", "kind", "policy_version", "target_type", "work_id",
                   "member_id", "target_id", "reason", "created_by", "status", "revision", "expires_ms", "created_at", "sequence")}
        result.update(old=json.loads(row["old_json"]), new=json.loads(row["new_json"]))
        result["reason"] = self.auth.redactor.clean(result["reason"])
        if row["kind"]=="responsibility_exception":
            for key in ("old","new"):
                result[key] = {"paths":[self.auth.redactor.clean(path) for path in result[key]["paths"]]}
        elif row["kind"]=="criteria_change":
            for key in ("old","new"): result[key] = self._clean_objective(result[key])
        result["decision"] = None
        if "decision_actor" in row.keys() and row["decision_actor"] is not None:
            result["decision"] = {"actor": row["decision_actor"], "decision": row["decision_value"], "reason": self.auth.redactor.clean(row["decision_reason"]),
                                  "policy_version": row["applied_policy_version"], "created_at": row["decided_at"]}
        return result

    def _clean_objective(self, value):
        return value | {"goal":self.auth.redactor.clean(value["goal"]),
                        "criteria":[self.auth.redactor.clean(text) for text in value["criteria"]]}

    @staticmethod
    def _allowed(runs, db, row):
        if runs._team(db, row["team_id"])["status"] != "draft":
            conflict("team_archived", "Archived team cannot change policy")
        if row["status"] in ("completed", "stopping"):
            conflict("policy_change_closed", "This run state cannot accept a policy change")

    def create_request(self, token, run_id, request_id, value):
        runs = self._runs(token, "request")
        run_id = opaque_id(run_id)
        strict_fields(value, ("kind", "old", "new", "policy_version", "reason", "expected_revision"), ("work_id","member_id","repair_root_id"))
        kind = value["kind"]
        if not isinstance(kind,str) or kind not in APPROVAL_KINDS:
            raise CollaborationError(400,"unsupported_approval_kind","Approval kind has no implemented target validator")
        if kind!="responsibility_exception" and "member_id" in value:
            raise CollaborationError(400,"invalid_fields","This target does not accept a member selector")
        root_id = opaque_id(value["repair_root_id"]) if "repair_root_id" in value else None
        repair_limits.validate_target(kind,value["old"],root_id)
        integer(value["policy_version"]); integer(value["expected_revision"], minimum=0)
        old,new = self._typed_change(kind,value["old"],value["new"])
        if kind=="criteria_change": new = self._clean_objective(new)
        reason = self._text(value["reason"])
        work_id = None if value.get("work_id") is None else opaque_id(value["work_id"])
        member_id = None if value.get("member_id") is None else opaque_id(value["member_id"])
        if kind=="responsibility_exception" and (work_id is None or member_id is None):
            raise CollaborationError(400,"responsibility_target_required","Select an exact member and work item")
        def authorize(db):
            self.access.principal(token, "request"); runs._run(db, run_id)
        def operation(db):
            run = runs._run(db, run_id)
            self._allowed(runs, db, run)
            self._typed_change(kind,old,new,admitting=True)
            if kind!="budget_extension" and run["status"] not in ("queued","running"):
                conflict("policy_change_closed","Only active runs can request capability or responsibility changes")
            if run["revision"] != value["expected_revision"]:
                conflict("stale_revision", "Run revision changed before the request")
            if run["policy_version"] != value["policy_version"]:
                conflict("stale_policy", "Request must target the current policy version")
            if run["status"] in ("queued", "running") and runs._wall_expired(db, run_id):
                return BudgetRepository._error(409, "policy_change_closed", "Budget exhaustion closed new policy requests")
            baseline = self._baseline(db,run,kind,old,work_id,member_id,root_id=root_id)
            if any(baseline[key]!=old[key] for key in old):
                conflict("budget_baseline_changed" if kind=="budget_extension" else "approval_baseline_changed", "Old values do not match the exact target")
            if kind=="criteria_change" and FrozenJSON(new).sha256==baseline["hash"]:
                raise CollaborationError(400,"criteria_change_required","New objective must differ from the current version")
            stored_old = self._clean_objective(baseline) if kind=="criteria_change" else old
            if work_id is not None and not db.execute("SELECT 1 FROM collab_work_items WHERE id=? AND run_id=?", (work_id, run_id)).fetchone():
                raise CollaborationError(403, "work_mismatch", "Related work must belong to this run")
            identifier = str(uuid4())
            db.execute("INSERT INTO collab_approval_requests(id,run_id,attempt_id,kind,policy_version,target_type,old_json,new_json,reason,work_id,"
                       "created_by,status,expires_ms,created_at,member_id,target_id) VALUES (?,?,?,?,?,?,?,?,?,?,'admin','pending',?,?,?,?)",
                       (identifier, run_id, run["current_attempt_id"], kind,run["policy_version"],
                        "repair_root" if root_id is not None else "work_member" if kind=="responsibility_exception" else "work_item" if kind=="criteria_change" and work_id is not None else "team_run",FrozenJSON(stored_old).json,
                        FrozenJSON(new).json, reason, work_id, runs.clock_ms()+self.ttl_ms, utc_now(),member_id,
                        root_id or member_id or (work_id if kind=="criteria_change" and work_id is not None else run_id)))
            changed = db.execute("UPDATE collab_runs SET revision=revision+1 WHERE id=? AND revision=?", (run_id, run["revision"]))
            if changed.rowcount != 1:
                conflict("stale_revision", "Run changed while creating the request")
            self.store.event(db, runs.scope, run_id, "approval_requested", {"approval_id": identifier, "kind": kind, "actor": "admin"})
            return {**self._dto(self._approval(db, run_id, identifier)), "run_revision": run["revision"]+1}
        result = self.store.mutate(runs.scope, request_id, "request_"+kind, run_id, {"input_hash": FrozenJSON(value).sha256},
                                   authorize=authorize, operation=operation)
        result = BudgetRepository._raise_error(result)
        # Apply current secret filtering even when reading an immutable replay.
        result["reason"] = self.auth.redactor.clean(result["reason"])
        if kind=="responsibility_exception":
            for key in ("old","new"):
                result[key] = {"paths":[self.auth.redactor.clean(path) for path in result[key]["paths"]]}
        elif kind=="criteria_change":
            for key in ("old","new"): result[key] = self._clean_objective(result[key])
        return result

    def _close(self, db, runs, row, status, *, touch_run=True):
        db.execute("UPDATE collab_approval_requests SET status=?,revision=revision+1 WHERE id=? AND status='pending'", (status, row["id"]))
        if touch_run:
            db.execute("UPDATE collab_runs SET revision=revision+1 WHERE id=?", (row["run_id"],))
        self.store.event(db, runs.scope, row["run_id"], "approval_"+status, {"approval_id": row["id"], "actor": "admin"})

    def decide(self, token, run_id, request_id, value):
        runs = self._runs(token, "approve")
        run_id = opaque_id(run_id)
        strict_fields(value, ("approval_id", "decision", "expected_revision", "reason"))
        approval_id = opaque_id(value["approval_id"])
        integer(value["expected_revision"], minimum=0)
        if value["decision"] not in ("approve", "deny"):
            raise CollaborationError(400, "invalid_approval_decision", "Choose approve or deny")
        reason = self._text(value["reason"])
        with self.store.transaction(write=False) as db:
            self.access.principal(token,"approve"); runs._run(db,run_id)
            kind = self._approval(db,run_id,approval_id)["kind"]
        if kind not in APPROVAL_KINDS:
            raise CollaborationError(400,"unsupported_approval_kind","Approval kind has no implemented target validator")
        def authorize(db):
            self.access.principal(token, "approve"); runs._run(db, run_id); self._approval(db, run_id, approval_id)
        def operation(db):
            run, row = runs._run(db, run_id), self._approval(db, run_id, approval_id)
            if row["status"] != "pending":
                conflict("approval_closed", "Approval request already has a terminal outcome")
            if row["revision"] != value["expected_revision"]:
                conflict("stale_revision", "Approval request revision has changed")
            self._allowed(runs, db, run)
            if kind!="budget_extension" and run["status"] not in ("queued","running"):
                conflict("policy_change_closed","This run cannot apply a capability or responsibility change")
            if run["status"] in ("queued", "running") and runs._wall_expired(db, run_id):
                return BudgetRepository._error(409, "approval_closed", "Budget exhaustion closed this approval")
            if runs.clock_ms() >= row["expires_ms"]:
                self._close(db, runs, row, "expired")
                return BudgetRepository._error(409, "approval_expired", "Approval request has expired")
            if row["attempt_id"] != run["current_attempt_id"] or row["policy_version"] != run["policy_version"]:
                self._close(db, runs, row, "superseded")
                return BudgetRepository._error(409, "approval_stale", "Approval targets an obsolete run attempt or policy")
            old, new = json.loads(row["old_json"]), json.loads(row["new_json"])
            root_id = row["target_id"] if row["target_type"]=="repair_root" else None
            repair_limits.validate_target(kind,old,root_id)
            policy = json.loads(run["policy_json"])
            try:
                baseline = self._baseline(db,run,kind,old,row["work_id"],row["member_id"],root_id=root_id)
            except CollaborationError as error:
                if error.code!="work_closed": raise
                baseline = None
            comparable = ("version","hash") if kind=="criteria_change" else tuple(old)
            if baseline is None or any(baseline[key]!=old[key] for key in comparable):
                self._close(db, runs, row, "superseded")
                return BudgetRepository._error(409, "approval_stale", "Approval budget baseline changed")
            version = None
            objective = None
            repair_limit = None
            if value["decision"] == "approve":
                admitted_old = {key:old[key] for key in ("version","hash")} if kind=="criteria_change" else old
                try:
                    self._typed_change(kind,admitted_old,new,admitting=True)  # Recheck runtime support at decision time.
                except CollaborationError as error:
                    if error.code!="criteria_redaction_changed": raise
                    self._close(db,runs,row,"superseded")
                    return BudgetRepository._error(error.status,error.code,error.message)
                if kind=="budget_extension" and root_id is None:
                    policy["limits"].update(new)
                elif kind=="capability":
                    policy.update(new)
                frozen = FrozenJSON(PolicySpec.from_dict(policy).to_dict())
                version = run["policy_version"]+1
                db.execute("INSERT INTO collab_policies VALUES (?,?,?,'admin',?,?,?,?)",
                           (run_id, version, frozen.json, kind, approval_id, reason, utc_now()))
                if kind=="responsibility_exception":
                    db.execute("INSERT INTO collab_responsibility_versions VALUES (?,?,?,?,?,?,?)",
                               (approval_id,run_id,run["current_attempt_id"],row["work_id"],row["member_id"],version,FrozenJSON(new["paths"]).json))
                elif kind=="criteria_change":
                    objective = criteria.append(db,run,row["work_id"],approval_id,new)
                elif root_id is not None:
                    repair_limit = repair_limits.append(db,run,root_id,approval_id,new)
                changed = db.execute("UPDATE collab_runs SET policy_json=?,policy_version=?,revision=revision+1 "
                                     "WHERE id=? AND policy_version=? AND revision=? AND status=?",
                                     (frozen.json, version, run_id, run["policy_version"], run["revision"], run["status"]))
                if changed.rowcount != 1:
                    conflict("stale_revision", "Run changed before policy publication")
            self._close(db, runs, row, "approved" if version is not None else "denied", touch_run=version is None)
            db.execute("INSERT INTO collab_approval_decisions VALUES (?,?,'admin',?,?,?,?)",
                       (approval_id, run_id, value["decision"], reason, version, utc_now()))
            result = {"approval_id": approval_id, "status": "approved" if version is not None else "denied", "policy_version": version,
                      "run_revision": run["revision"]+1}
            if objective is not None:
                self.store.event(db,runs.scope,run_id,"criteria_changed",{"approval_id":approval_id,"target_type":objective["target_type"],
                    "target_id":objective["target_id"],"old_version":old["version"],"version":objective["version"],"hash":objective["hash"]})
                result.update(criteria_version=objective["version"],criteria_hash=objective["hash"])
            if repair_limit is not None:
                self.store.event(db,runs.scope,run_id,"repair_limit_extended",{"approval_id":approval_id,"root_id":root_id,
                    "version":repair_limit["limit_version"],"old_total":old["repair_attempts"],"new_total":new["repair_attempts"]})
                result.update(repair_root_id=root_id,repair_limit_version=repair_limit["limit_version"])
            return result
        result = self.store.mutate(runs.scope, request_id, "decide_"+kind, run_id, {"input_hash": FrozenJSON(value).sha256},
                                   authorize=authorize, operation=operation)
        return BudgetRepository._raise_error(result)

    def list_requests(self, token, run_id, *, after_sequence=0, limit=50):
        runs = self._runs(token, "read"); run_id = opaque_id(run_id)
        integer(after_sequence, minimum=0, maximum=2**63-1); integer(limit, maximum=200)
        with self.store.transaction(write=False) as db:
            self.access.principal(token, "read"); runs._run(db, run_id)
            rows = db.execute("SELECT a.*,d.actor decision_actor,d.decision decision_value,d.reason decision_reason,d.applied_policy_version,d.created_at decided_at "
                              "FROM collab_approval_requests a LEFT JOIN collab_approval_decisions d ON d.approval_id=a.id "
                              "WHERE a.run_id=? AND a.sequence>? ORDER BY a.sequence LIMIT ?", (run_id, after_sequence, limit+1)).fetchall()
            selected = rows[:limit]
            return {"items": [self._dto(row) for row in selected], "next_cursor": selected[-1]["sequence"] if selected else after_sequence,
                    "has_more": len(rows)>limit}

    def list_policies(self, token, run_id, *, after_version=0, limit=50):
        runs = self._runs(token, "read"); run_id = opaque_id(run_id)
        integer(after_version, minimum=0); integer(limit, maximum=200)
        with self.store.transaction(write=False) as db:
            self.access.principal(token, "read"); runs._run(db, run_id)
            rows = db.execute("SELECT * FROM collab_policies WHERE run_id=? AND version>? ORDER BY version LIMIT ?",
                              (run_id, after_version, limit+1)).fetchall()
            selected = rows[:limit]
            return {"items": [{**{key:row[key] for key in row.keys() if key!="payload"}, "reason":self.auth.redactor.clean(row["reason"]), "policy": json.loads(row["payload"]),
                               "hash": FrozenJSON(json.loads(row["payload"])).sha256} for row in selected],
                    "next_cursor": selected[-1]["version"] if selected else after_version, "has_more": len(rows)>limit}

    def list_criteria(self, token, run_id, *, work_id=None, after_version=0, limit=50):
        runs = self._runs(token,"read"); run_id = opaque_id(run_id)
        integer(after_version,minimum=0); integer(limit,maximum=200)
        with self.store.transaction(write=False) as db:
            self.access.principal(token,"read"); runs._run(db,run_id)
            kind,identifier = criteria.target(db,run_id,work_id)
            rows = db.execute("SELECT * FROM collab_criteria_versions WHERE run_id=? AND target_type=? AND target_id=? AND version>? ORDER BY version LIMIT ?",
                              (run_id,kind,identifier,after_version,limit+1)).fetchall()
            items = [self._clean_objective(criteria.dto(row)) for row in rows[:limit]]
            return {"items":items,"has_more":len(rows)>limit,"next_cursor":items[-1]["version"] if items else after_version}

    def list_repair_limits(self, token, run_id, root_id, *, after_version=0, limit=50):
        runs = self._runs(token,"read"); run_id,root_id = opaque_id(run_id),opaque_id(root_id)
        integer(after_version,minimum=0); integer(limit,maximum=200)
        with self.store.transaction(write=False) as db:
            self.access.principal(token,"read"); runs._run(db,run_id)
            current = repair_limits.current(db,run_id,root_id)
            rows = db.execute("SELECT * FROM collab_repair_limit_versions WHERE root_id=? AND run_id=? AND version>? ORDER BY version LIMIT ?",
                              (root_id,run_id,after_version,limit+1)).fetchall()
            items = [dict(row) for row in rows[:limit]]
            return {"current":current,"items":items,"has_more":len(rows)>limit,"next_cursor":items[-1]["version"] if items else after_version}

    def covers_responsibility(self, binding, work_id, changed_paths):
        """Trusted submission coordinator check; never a filesystem capability.

        Caller must separately validate its immutable manifest, forced file
        policy, private workspace ownership and publication/validation gates.
        """
        work_id = opaque_id(work_id); paths = self._paths(changed_paths)
        runs = RunRepository(self.store,self._scope_for_binding(binding))
        with self.store.transaction(write=False) as db:
            run = runs.authorize_binding(db,binding)
            if binding.actor!="member":
                raise CollaborationError(403,"member_required","Responsibility is bound to a member")
            if run["status"]!="running":
                conflict("dispatch_closed","Run is not accepting submissions")
            if BudgetRepository.pending_for_actor(db,binding):
                conflict("actor_operation_unknown","Reconcile this actor before submission")
            allowed,status = self._responsibility(db,run,work_id,binding.member_id)
            if status in ("completed","failed","cancelled","interrupted"):
                conflict("work_closed","Responsibility target work is terminal")
            return all(any(self._covers(prefix,path) for prefix in allowed) for path in paths)

    def _scope_for_binding(self, binding):
        # The coordinator derives workspace/admin identity, never model input.
        from .runs import RunBinding
        from .storage import Scope
        if not isinstance(binding,RunBinding):
            raise CollaborationError(403,"invalid_binding","A service-bound member is required")
        return Scope("admin",self.access.workspace)
