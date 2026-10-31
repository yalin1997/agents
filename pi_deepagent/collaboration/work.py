"""Service-owned work plans and initial attempt admission, without execution.

Context factories are coordinator-only. A model tool receives an already issued
context in its closure; parent/root IDs, requiredness and context identity are
not model arguments. Future repair scopes must be derived from persisted
failure/candidate evidence by the supervisor, never from a model-selected root.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import json
from uuid import uuid4

from .budgets import BudgetRepository
from .contracts import CollaborationError, FrozenJSON, integer, opaque_id, required_text, strict_fields
from .runs import RunBinding, conflict
from . import criteria as objectives


@dataclass(frozen=True, slots=True)
class PlanningContext:
    id: str
    binding: RunBinding

    def __post_init__(self):
        object.__setattr__(self,"id",opaque_id(self.id))
        if not isinstance(self.binding,RunBinding):
            raise CollaborationError(403,"invalid_binding","A service-bound planning actor is required")


class WorkRepository:
    def __init__(self, runs, redactor):
        self.runs,self.store,self.scope = runs,runs.store,runs.scope
        self.redactor = redactor

    def _actor(self, db, binding):
        run = self.runs.authorize_binding(db,binding)
        row = db.execute("SELECT spec_json FROM collab_members WHERE id=? AND run_id=?",(binding.member_id,binding.run_id)).fetchone()
        if binding.actor!="member" or row is None or json.loads(row["spec_json"]).get("orchestrator") is not True:
            raise CollaborationError(403,"orchestrator_required","Planning requires the service-bound Orchestrator")
        return run

    def _dispatch(self, db, binding):
        run = self._actor(db,binding)
        if run["status"]!="running":
            return BudgetRepository._error(409,"dispatch_closed","Run is not accepting work plans")
        if self.runs._wall_expired(db,run["id"]):
            return BudgetRepository._error(429,"budget_exhausted","Run wall-time budget is exhausted")
        if BudgetRepository.pending_for_actor(db,binding):
            conflict("actor_operation_unknown","Reconcile the planning actor before another operation")
        return None

    @staticmethod
    def _work(db, run_id, work_id):
        row = db.execute("SELECT * FROM collab_work_items WHERE id=?",(opaque_id(work_id),)).fetchone()
        if row is None: raise CollaborationError(404,"work_not_found","Work item was not found")
        if row["run_id"]!=run_id: raise CollaborationError(403,"work_mismatch","Work item does not belong to this run")
        return row

    @staticmethod
    def _ids(value):
        if not isinstance(value,list) or len(value)>200:
            raise CollaborationError(400,"invalid_dependencies","Dependencies must be a bounded array")
        result = [opaque_id(item) for item in value]
        if len(set(result))!=len(result):
            raise CollaborationError(400,"duplicate_dependency","Dependency IDs must be unique")
        return sorted(result)

    def _text(self, value):
        required_text(value)
        cleaned = self.redactor.clean(value)
        if len(value)>32000 or len(cleaned)>32000:
            raise CollaborationError(400,"work_text_too_long","Work text exceeds the allowed limit")
        return cleaned

    def issue_initial_context(self, binding):
        """Trusted coordinator factory, not an Agent tool."""
        with self.store.transaction() as db:
            run = self._actor(db,binding)
            gate = db.execute("SELECT initial_open FROM collab_work_plan_gates WHERE run_id=?",(binding.run_id,)).fetchone()
            if run["status"]!="running" or gate is None or not gate[0]:
                conflict("initial_plan_sealed","Initial planning is closed for this run")
            return self._issue(db,binding,"initial",[],[])

    def issue_child_context(self, binding, source_work_ids):
        """Sources come from trusted coordinator state, not tool JSON.

        This binds ordinary child decomposition. It does not register a repair
        plan, consume repair quota, or substitute for failure evidence checks.
        """
        parents = self._ids(source_work_ids)
        if not parents: raise CollaborationError(400,"missing_parent","Child planning requires a source work item")
        with self.store.transaction() as db:
            run = self._actor(db,binding)
            if run["status"]!="running": conflict("dispatch_closed","Run is not accepting child planning")
            roots = set()
            for parent in parents:
                self._work(db,binding.run_id,parent)
                inherited = db.execute("SELECT root_id FROM collab_work_roots WHERE work_id=? AND run_id=?",(parent,binding.run_id)).fetchall()
                if not inherited: conflict("repair_root_missing","Source work has no authoritative repair root")
                roots.update(row[0] for row in inherited)
            return self._issue(db,binding,"child",parents,sorted(roots))

    @staticmethod
    def _issue(db, binding, kind, parents, roots):
        context = PlanningContext(str(uuid4()),binding)
        db.execute("INSERT INTO collab_planning_contexts(id,run_id,attempt_id,member_id,binding_json,kind,parents_json,roots_json) VALUES (?,?,?,?,?,?,?,?)",
                   (context.id,binding.run_id,binding.attempt_id,binding.member_id,FrozenJSON(asdict(binding)).json,kind,
                    FrozenJSON(parents).json,FrozenJSON(roots).json))
        return context

    def _context(self, db, context):
        if not isinstance(context,PlanningContext):
            raise CollaborationError(403,"invalid_context","A service-issued planning context is required")
        self._actor(db,context.binding)
        row = db.execute("SELECT * FROM collab_planning_contexts WHERE id=? AND run_id=?",(context.id,context.binding.run_id)).fetchone()
        if row is None or not row["active"] or row["binding_json"]!=FrozenJSON(asdict(context.binding)).json:
            raise CollaborationError(403,"invalid_context","Planning context is altered, expired or revoked")
        return row

    def _references(self, db, run_id, dependencies):
        for dependency in dependencies: self._work(db,run_id,dependency)

    @staticmethod
    def _acyclic(db, run_id, work_id, dependencies):
        graph = {row[0]:set() for row in db.execute("SELECT id FROM collab_work_items WHERE run_id=?",(run_id,))}
        for row in db.execute("SELECT work_id,depends_on FROM collab_work_dependencies WHERE run_id=?",(run_id,)):
            graph[row[0]].add(row[1])
        graph[work_id] = set(dependencies)
        successors = {key:[] for key in graph}
        counts = {key:len(value) for key,value in graph.items()}
        for node,parents in graph.items():
            for parent in parents: successors[parent].append(node)
        ready = deque(key for key,count in counts.items() if count==0)
        visited = 0
        while ready:
            node = ready.popleft(); visited += 1
            for target in successors[node]:
                counts[target] -= 1
                if counts[target]==0: ready.append(target)
        if visited!=len(graph): conflict("dependency_cycle","Work dependencies must form a DAG")

    @staticmethod
    def _ready(db, run_id, dependencies):
        return all(db.execute("SELECT status FROM collab_work_items WHERE id=? AND run_id=?",(item,run_id)).fetchone()[0]=="completed"
                   for item in dependencies)

    def _detail(self, db, run_id, work_id):
        row = self._work(db,run_id,work_id)
        objective = objectives.current(db,run_id,work_id)
        return {key:row[key] for key in row.keys() if key!="criteria_json"} | {
            "goal":self.redactor.clean(objective["goal"]),
            "criteria":[self.redactor.clean(item) for item in objective["criteria"]],
            "criteria_version":objective["version"],"criteria_hash":objective["hash"],
            "dependencies":[item[0] for item in db.execute("SELECT depends_on FROM collab_work_dependencies WHERE work_id=? ORDER BY depends_on",(work_id,))],
            "roots":[dict(item) for item in db.execute("SELECT r.* FROM collab_work_roots w JOIN collab_repair_roots r ON r.id=w.root_id "
                       "WHERE w.work_id=? ORDER BY r.id",(work_id,))],
            "sources":[item[0] for item in db.execute("SELECT source_work_id FROM collab_work_origins WHERE work_id=? ORDER BY source_work_id",(work_id,))]}

    def get(self, run_id, work_id):
        with self.store.transaction(write=False) as db:
            self.runs._run(db,run_id)
            return self._detail(db,run_id,work_id)

    def _response(self, result):
        result = BudgetRepository._raise_error(result)
        target = result.get("inputs",result)
        cleaned = target | {"goal":self.redactor.clean(target["goal"]),
                            "criteria":[self.redactor.clean(item) for item in target["criteria"]]}
        return result | {"inputs":cleaned} if "inputs" in result else cleaned

    def create(self, context, request_id, value):
        if not isinstance(context,PlanningContext):
            raise CollaborationError(403,"invalid_context","A service-issued planning context is required")
        strict_fields(value,("goal","criteria"),("dependencies",))
        goal = self._text(value["goal"])
        if not isinstance(value["criteria"],list) or not value["criteria"] or len(value["criteria"])>200:
            raise CollaborationError(400,"invalid_work_criteria","Work requires explicit acceptance criteria")
        criteria = [self._text(item) for item in value["criteria"]]
        dependencies = self._ids(value.get("dependencies",[]))
        binding = context.binding
        def operation(db):
            scope = self._context(db,context)
            error = self._dispatch(db,binding)
            if error: return error
            if scope["kind"]=="initial" and not db.execute("SELECT initial_open FROM collab_work_plan_gates WHERE run_id=?",(binding.run_id,)).fetchone()[0]:
                conflict("initial_plan_sealed","Initial planning is closed")
            self._references(db,binding.run_id,dependencies)
            identifier = str(uuid4())
            self._acyclic(db,binding.run_id,identifier,dependencies)
            parents,roots = json.loads(scope["parents_json"]),json.loads(scope["roots_json"])
            primary_root = identifier if scope["kind"]=="initial" else roots[0]
            status = "ready" if self._ready(db,binding.run_id,dependencies) else "pending"
            db.execute("INSERT INTO collab_work_items(id,run_id,parent_id,repair_root_id,goal,criteria_json,required,status) VALUES (?,?,?,?,?,?,1,?)",
                       (identifier,binding.run_id,parents[0] if parents else None,primary_root,goal,FrozenJSON(criteria).json,status))
            if scope["kind"]=="child":
                db.executemany("INSERT INTO collab_work_roots VALUES (?,?,?)",[(identifier,binding.run_id,root) for root in roots])
                db.executemany("INSERT INTO collab_work_origins VALUES (?,?,?)",[(identifier,parent,binding.run_id) for parent in parents])
            db.executemany("INSERT INTO collab_work_dependencies VALUES (?,?,?)",[(identifier,item,binding.run_id) for item in dependencies])
            db.execute("UPDATE collab_runs SET revision=revision+1 WHERE id=?",(binding.run_id,))
            self.store.event(db,self.scope,binding.run_id,"work_created",{"work_id":identifier,"member_id":binding.member_id,"required":True})
            return self._detail(db,binding.run_id,identifier)
        result = self.store.mutate(self.scope,request_id,"create_work",binding.run_id,{"context":context.id,"input_hash":FrozenJSON(value).sha256},
                                  authorize=lambda db:self._context(db,context),operation=operation)
        # Replayed results are immutable in the ledger, but newly registered
        # secrets must also be hidden when reading an earlier response.
        return self._response(result)

    def set_dependencies(self, binding, work_id, request_id, dependencies, *, expected_revision):
        work_id = opaque_id(work_id); dependencies = self._ids(dependencies); integer(expected_revision,minimum=0)
        def authorize(db): self._actor(db,binding); self._work(db,binding.run_id,work_id)
        def operation(db):
            error = self._dispatch(db,binding)
            if error: return error
            work = self._work(db,binding.run_id,work_id)
            if work["revision"]!=expected_revision: conflict("stale_revision","Work revision has changed")
            if work["status"] not in ("pending","ready","repair_pending") or db.execute("SELECT 1 FROM collab_work_attempts WHERE work_id=? "
                    "AND status IN ('queued','running','submitted','verifying')",(work_id,)).fetchone():
                conflict("work_busy","Dependencies cannot change for active or terminal work")
            self._references(db,binding.run_id,dependencies)
            self._acyclic(db,binding.run_id,work_id,dependencies)
            db.execute("DELETE FROM collab_work_dependencies WHERE work_id=?",(work_id,))
            db.executemany("INSERT INTO collab_work_dependencies VALUES (?,?,?)",[(work_id,item,binding.run_id) for item in dependencies])
            status = work["status"] if work["status"]=="repair_pending" else (
                "ready" if self._ready(db,binding.run_id,dependencies) else "pending")
            changed = db.execute("UPDATE collab_work_items SET status=?,revision=revision+1 WHERE id=? AND revision=?",(status,work_id,expected_revision))
            if changed.rowcount!=1: conflict("stale_revision","Work changed during dependency update")
            db.execute("UPDATE collab_runs SET revision=revision+1 WHERE id=?",(binding.run_id,))
            self.store.event(db,self.scope,binding.run_id,"dependencies_changed",{"work_id":work_id,"dependencies":dependencies})
            return self._detail(db,binding.run_id,work_id)
        result = self.store.mutate(self.scope,request_id,"set_dependencies",binding.run_id,
            {"binding":asdict(binding),"work_id":work_id,"dependencies":dependencies,"expected_revision":expected_revision},authorize=authorize,operation=operation)
        return self._response(result)

    def prepare_initial_attempt(self, binding, work_id, member_id, request_id, *, expected_revision):
        work_id,member_id = opaque_id(work_id),opaque_id(member_id); integer(expected_revision,minimum=0)
        def authorize(db): self._actor(db,binding); self._work(db,binding.run_id,work_id)
        def operation(db):
            error = self._dispatch(db,binding)
            if error: return error
            work = self._work(db,binding.run_id,work_id)
            if work["revision"]!=expected_revision: conflict("stale_revision","Work revision has changed")
            if not db.execute("SELECT 1 FROM collab_members WHERE id=? AND run_id=?",(member_id,binding.run_id)).fetchone():
                raise CollaborationError(403,"member_mismatch","Assignee must belong to this run")
            if db.execute("SELECT 1 FROM collab_work_attempts WHERE work_id=?",(work_id,)).fetchone():
                conflict("repair_or_continue_required","Existing work requires the repair or continuation protocol")
            if work["status"] not in ("pending","ready"): conflict("invalid_work_state","Work cannot be initially assigned")
            detail = self._detail(db,binding.run_id,work_id)
            if not self._ready(db,binding.run_id,detail["dependencies"]): conflict("dependencies_pending","Dependencies are not complete")
            identifier = str(uuid4())
            inputs = {"goal":detail["goal"],"criteria":detail["criteria"],"dependencies":detail["dependencies"],
                      "root_ids":[row["id"] for row in detail["roots"]],"policy_version":binding.policy_version,
                      "criteria_refs":objectives.snapshot(db,binding.run_id,work_id)}
            db.execute("INSERT INTO collab_work_attempts(id,work_id,run_id,run_attempt_id,member_id,number,status,inputs_json) "
                       "VALUES (?,?,?,?,?,1,'queued',?)",(identifier,work_id,binding.run_id,binding.attempt_id,member_id,FrozenJSON(inputs).json))
            changed = db.execute("UPDATE collab_work_items SET status='ready',revision=revision+1 WHERE id=? AND revision=?",(work_id,expected_revision))
            if changed.rowcount!=1: conflict("stale_revision","Work changed during attempt preparation")
            db.execute("UPDATE collab_runs SET revision=revision+1 WHERE id=?",(binding.run_id,))
            self.store.event(db,self.scope,binding.run_id,"work_attempt_prepared",{"work_id":work_id,"work_attempt_id":identifier,"member_id":member_id})
            return {"id":identifier,"work_id":work_id,"run_attempt_id":binding.attempt_id,"member_id":member_id,"status":"queued","number":1,"revision":0,"inputs":inputs}
        result = self.store.mutate(self.scope,request_id,"prepare_initial_work_attempt",binding.run_id,
            {"binding":asdict(binding),"work_id":work_id,"member_id":member_id,"expected_revision":expected_revision},authorize=authorize,operation=operation)
        return self._response(result)
