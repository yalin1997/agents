"""Real SQLite work planning. Fixture completions are not graph/gate evidence."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from threading import Barrier
from uuid import uuid4

from pi_deepagent.collaboration.contracts import CollaborationError
from pi_deepagent.collaboration.runs import RunRepository
from pi_deepagent.collaboration.state import EntityKind, VersionedState
from pi_deepagent.collaboration.storage import CollaborationStore, migrate_reviewed, SCHEMA_VERSION, Scope
from pi_deepagent.collaboration.work import PlanningContext, WorkRepository
from tests.collaboration.test_runs import RunFixture


class WorkTests(RunFixture):
    def setUp(self):
        super().setUp()
        self.activate()
        self.work = WorkRepository(self.runs,self.auth.redactor)
        self.bind_actors()
        self.context = self.work.issue_initial_context(self.lead)

    def bind_actors(self):
        members = self.runs.get(self.run["id"])["members"]
        self.lead = self.runs.bind_member(self.run["id"],next(m["id"] for m in members if m["spec"]["orchestrator"]))
        self.peer = self.runs.bind_member(self.run["id"],next(m["id"] for m in members if not m["spec"]["orchestrator"]))

    def rows(self,sql,params=()):
        with self.store.transaction(write=False) as db: return [dict(row) for row in db.execute(sql,params)]

    def create(self,goal="Implement a real requirement",dependencies=None,context=None,request=None):
        return self.work.create(context or self.context,request or str(uuid4()),
                                {"goal":goal,"criteria":["Explicit evidence"],"dependencies":dependencies or []})

    def assign(self,item,**kw):
        return self.work.prepare_initial_attempt(self.lead,item["id"],kw.pop("member",self.peer.member_id),
                                                kw.pop("request",str(uuid4())),expected_revision=kw.pop("revision",item["revision"]),**kw)

    def error(self,code,call):
        with self.assertRaises(CollaborationError) as raised: call()
        self.assertEqual(raised.exception.code,code)
        return raised.exception

    def test_create_replay_snapshot_and_requiredness_are_service_owned(self):
        request = str(uuid4()); item = self.create(request=request)
        self.assertEqual(self.create(request=request),item)
        self.assertEqual((item["status"],item["required"],item["revision"]),("ready",1,0))
        self.assertEqual(item["repair_root_id"],item["id"])
        self.assertEqual([(r["id"],r["used"],r["reserved"],r["limit_total"]) for r in item["roots"]],[(item["id"],0,0,3)])
        self.assertEqual(item["sources"],[])
        self.assertEqual(len(self.rows("SELECT * FROM collab_work_items")),1)
        self.assertEqual(len(self.rows("SELECT * FROM collab_events WHERE kind='work_created'")),1)
        self.error("request_conflict",lambda:self.create(goal="Different",request=request))
        for field,value in [("required",False),("root_id",str(uuid4())),("parent_id",item["id"]),("actor","admin")]:
            with self.subTest(field=field):
                self.error("invalid_fields",lambda:self.work.create(self.context,str(uuid4()),{"goal":"x","criteria":["y"],field:value}))

    def test_orchestrator_and_exact_server_context_required_before_replay(self):
        item = self.create(); request = str(uuid4()); self.create(request=request)
        self.error("orchestrator_required",lambda:self.work.issue_initial_context(self.peer))
        for context,code in [(None,"invalid_context"),(PlanningContext(str(uuid4()),self.lead),"invalid_context"),
                             (replace(self.context,binding=self.peer),"orchestrator_required"),
                             (replace(self.context,binding=replace(self.lead,grant_id=str(uuid4()))),"invalid_binding"),
                             (replace(self.context,binding=self.runs.bind_member(self.run["id"],self.lead.member_id)),"invalid_context")]:
            with self.subTest(code=code): self.error(code,lambda:self.work.create(context,str(uuid4()),{"goal":"x","criteria":["y"]}))
        with self.store.transaction() as db: db.execute("UPDATE collab_actor_grants SET active=0 WHERE id=?",(self.lead.grant_id,))
        self.error("invalid_binding",lambda:self.create(request=request))
        self.error("invalid_binding",lambda:self.assign(item))

    def test_stale_policy_context_cannot_create_or_assign(self):
        item = self.create(); self.extend_limits({"messages":6})
        self.error("stale_policy",lambda:self.create())
        self.error("stale_policy",lambda:self.assign(item))
        self.bind_actors()
        self.assertEqual(self.assign(item)["status"],"queued")

    def test_redaction_at_write_get_and_ledger_replay(self):
        self.auth.redactor.add("known-secret")
        item = self.create(goal="known-secret later-secret")
        self.assertNotIn("known-secret",self.rows("SELECT goal FROM collab_work_items")[0]["goal"])
        self.auth.redactor.add("later-secret")
        self.assertNotIn("later-secret",self.work.get(self.run["id"],item["id"])["goal"])
        request = str(uuid4()); newer = self.create(goal="becomes-secret",request=request)
        self.auth.redactor.add("becomes-secret")
        self.assertNotIn("becomes-secret",self.create(goal="becomes-secret",request=request)["goal"])
        self.assertEqual(self.rows("SELECT goal FROM collab_work_items WHERE id=?",(newer["id"],))[0]["goal"],"becomes-secret")

    def test_dependencies_are_dag_and_failed_cycle_is_atomic(self):
        a = self.create(); b = self.create(dependencies=[a["id"]]); c = self.create(dependencies=[b["id"]])
        before = self.runs.get(self.run["id"])
        for dependencies in ([a["id"]],[c["id"]]):
            self.error("dependency_cycle",lambda:self.work.set_dependencies(self.lead,a["id"],str(uuid4()),dependencies,expected_revision=0))
        self.assertEqual(self.work.get(self.run["id"],a["id"])["dependencies"],[])
        self.assertEqual(self.runs.get(self.run["id"]),before)
        self.assertEqual(self.rows("SELECT * FROM collab_requests WHERE action='set_dependencies'"),[])

    def test_dependency_updates_derive_readiness_and_preserve_repair_pending(self):
        a = self.create(); b = self.create()
        changed = self.work.set_dependencies(self.lead,b["id"],str(uuid4()),[a["id"]],expected_revision=0)
        self.assertEqual((changed["status"],changed["revision"]),("pending",1))
        self.assertEqual(VersionedState(EntityKind.WORK_ITEM,"ready").transition("pending",expected_revision=0).status,"pending")
        with self.store.transaction() as db:
            db.execute("UPDATE collab_work_items SET status='repair_pending' WHERE id=?",(b["id"],))
        changed = self.work.set_dependencies(self.lead,b["id"],str(uuid4()),[],expected_revision=1)
        self.assertEqual(changed["status"],"repair_pending")
        self.error("invalid_work_state",lambda:self.assign(changed))

    def test_initial_attempt_waits_for_dependencies_then_captures_immutable_inputs(self):
        a = self.create(); b = self.create(dependencies=[a["id"]])
        self.error("dependencies_pending",lambda:self.assign(b))
        self.assertEqual(self.rows("SELECT * FROM collab_work_attempts"),[])
        # Trusted fixture state, not a claim that real verification has run.
        with self.store.transaction() as db: db.execute("UPDATE collab_work_items SET status='completed' WHERE id=?",(a["id"],))
        request = str(uuid4()); attempt = self.assign(b,request=request)
        self.assertEqual(attempt,self.assign(b,request=request))
        self.assertEqual((attempt["status"],attempt["number"],attempt["member_id"]),("queued",1,self.peer.member_id))
        self.assertEqual(attempt["inputs"],{"goal":b["goal"],"criteria":b["criteria"],"dependencies":[a["id"]],"root_ids":[b["id"]],"policy_version":1,
            "criteria_refs":{"team_criteria":{"version":1,"hash":"d33c5f324d2be189cac1d6ac707d94313e21c1a1d04450e1c1065ba2f975924a"},
                             "work_criteria":{"version":1,"hash":"f8d6e6cd1b520bf70ce677b7bfaf2619b0291d52a9fd70d7b5a625bab8b9740f"}}})
        for sql in ["UPDATE collab_work_attempts SET inputs_json='{}'","UPDATE collab_work_items SET required=0","UPDATE collab_work_items SET criteria_json='[]'"]:
            with self.subTest(sql=sql),self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as db: db.execute(sql)

    def test_active_work_attempt_unique_and_no_initial_attempt_can_bypass_old_attempt(self):
        item = self.create(); attempt = self.assign(item)
        self.error("work_busy",lambda:self.work.set_dependencies(self.lead,item["id"],str(uuid4()),[],expected_revision=1))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction() as db:
                db.execute("INSERT INTO collab_work_attempts(id,work_id,run_id,run_attempt_id,member_id,number,status,inputs_json) VALUES (?,?,?,?,?,2,'queued','{}')",
                           (str(uuid4()),item["id"],self.run["id"],self.lead.attempt_id,self.lead.member_id))
        with self.store.transaction() as db: db.execute("UPDATE collab_work_attempts SET status='failed',outcome_json=? WHERE id=?",('{"certainty":"known"}',attempt["id"]))
        old = self.rows("SELECT * FROM collab_work_attempts")
        self.error("repair_or_continue_required",lambda:self.assign(item,revision=1,member=self.lead.member_id))
        self.assertEqual(self.rows("SELECT * FROM collab_work_attempts"),old)

    def test_first_attempt_seals_initial_planning_across_continue(self):
        item = self.create(); second_context = self.work.issue_initial_context(self.lead)
        self.assign(item)
        self.error("initial_plan_sealed",lambda:self.work.issue_initial_context(self.lead))
        for context in [self.context,second_context]: self.error("invalid_context",lambda:self.create(context=context))
        with self.assertRaisesRegex(sqlite3.IntegrityError,"initial_plan_sealed"):
            with self.store.transaction() as db: db.execute("UPDATE collab_work_plan_gates SET initial_open=1")
        stopped = self.finish()
        self.run = self.runs.continue_run(self.run["id"],str(uuid4()),expected_revision=stopped["revision"])
        self.activate(); self.bind_actors()
        self.error("initial_plan_sealed",lambda:self.work.issue_initial_context(self.lead))
        self.assertEqual(self.rows("SELECT initial_open FROM collab_work_plan_gates"),[{"initial_open":0}])

    def test_child_and_grandchild_inherit_counter_without_trace_dag_edges(self):
        root = self.create()
        with self.store.transaction() as db: db.execute("UPDATE collab_repair_roots SET used=2,revision=1 WHERE id=?",(root["id"],))
        child = self.create(context=self.work.issue_child_context(self.lead,[root["id"]]))
        grandchild = self.create(context=self.work.issue_child_context(self.lead,[child["id"]]))
        for item,source in [(child,root),(grandchild,child)]:
            self.assertEqual(item["sources"],[source["id"]])
            self.assertEqual(item["repair_root_id"],root["id"])
            self.assertEqual([(r["id"],r["used"]) for r in item["roots"]],[(root["id"],2)])
            self.assertEqual((item["dependencies"],item["status"]),([],"ready"))
        self.assertEqual(len(self.rows("SELECT * FROM collab_repair_roots")),1)

    def test_multiple_sources_preserve_union_of_all_roots_and_origins(self):
        a = self.create(); b = self.create()
        child = self.create(context=self.work.issue_child_context(self.lead,[b["id"],a["id"]]))
        grandchild = self.create(context=self.work.issue_child_context(self.lead,[child["id"],a["id"]]))
        only_child = self.create(context=self.work.issue_child_context(self.lead,[child["id"]]))
        for item in [child,grandchild,only_child]: self.assertEqual([r["id"] for r in item["roots"]],sorted([a["id"],b["id"]]))
        self.assertEqual(child["sources"],sorted([a["id"],b["id"]]))
        self.assertEqual(grandchild["sources"],sorted([child["id"],a["id"]]))
        self.assertEqual(len(self.rows("SELECT * FROM collab_repair_roots")),2)

    def test_sql_root_identity_and_consumption_cannot_be_reset(self):
        root = self.create(); self.create(context=self.work.issue_child_context(self.lead,[root["id"]]))
        with self.store.transaction() as db: db.execute("UPDATE collab_repair_roots SET used=2")
        for sql in ["UPDATE collab_repair_roots SET used=0","UPDATE collab_repair_roots SET limit_total=0",
                    "DELETE FROM collab_work_roots","UPDATE collab_work_roots SET root_id=root_id",
                    "DELETE FROM collab_work_origins","UPDATE collab_work_origins SET source_work_id=source_work_id",
                    "UPDATE collab_planning_contexts SET roots_json='[]'"]:
            with self.subTest(sql=sql),self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as db: db.execute(sql)
        self.assertEqual(self.rows("SELECT used FROM collab_repair_roots"),[{"used":2}])

    def test_parallel_dependency_cycle_and_same_work_cas(self):
        a = self.create(); b = self.create()
        def race(pairs):
            barrier = Barrier(2)
            def update(pair):
                with CollaborationStore(self.path) as store:
                    work = WorkRepository(RunRepository(store,self.scope,clock_ms=lambda:self.now),self.auth.redactor)
                    barrier.wait(timeout=5)
                    try: return work.set_dependencies(self.lead,pair[0],str(uuid4()),pair[1],expected_revision=0)["revision"]
                    except CollaborationError as exc: return exc.code
            with ThreadPoolExecutor(max_workers=2) as pool: return list(pool.map(update,pairs))
        results = race([(a["id"],[b["id"]]),(b["id"],[a["id"]])])
        self.assertCountEqual(results,[1,"dependency_cycle"])
        c = self.create()
        self.assertCountEqual(race([(c["id"],[a["id"]]),(c["id"],[b["id"]])]),[1,"stale_revision"])

    def test_parallel_identical_create_and_attempt_replay_commit_only_once(self):
        barrier = Barrier(2); request = str(uuid4())
        def create(index):
            with CollaborationStore(self.path) as store:
                work = WorkRepository(RunRepository(store,self.scope,clock_ms=lambda:self.now),self.auth.redactor)
                barrier.wait(timeout=5)
                return work.create(self.context,request,{"goal":"same","criteria":["one"]})
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(create,range(2)))
        self.assertEqual(results[0],results[1]); self.assertEqual(len(self.rows("SELECT * FROM collab_work_items")),1)
        item = results[0]; barrier = Barrier(2); request = str(uuid4())
        def assign(index):
            with CollaborationStore(self.path) as store:
                work = WorkRepository(RunRepository(store,self.scope,clock_ms=lambda:self.now),self.auth.redactor)
                barrier.wait(timeout=5)
                return work.prepare_initial_attempt(self.lead,item["id"],self.peer.member_id,request,expected_revision=0)
        with ThreadPoolExecutor(max_workers=2) as pool: attempts = list(pool.map(assign,range(2)))
        self.assertEqual(attempts[0],attempts[1]); self.assertEqual(len(self.rows("SELECT * FROM collab_work_attempts")),1)

    def test_dispatch_wall_expiry_persists_stop_intent_before_error(self):
        self.now += 10000; request = str(uuid4())
        self.error("budget_exhausted",lambda:self.create(request=request))
        self.error("budget_exhausted",lambda:self.create(request=request))
        run = self.runs.get(self.run["id"])
        self.assertEqual((run["status"],run["terminal_intent"],run["wall_used_ms"]),("stopping","failed",10000))
        self.assertEqual(self.rows("SELECT * FROM collab_work_items"),[])
        self.assertEqual(len(self.rows("SELECT * FROM collab_events WHERE kind='budget_exhausted'")),1)

    def test_unknown_actor_blocks_work_with_new_context_and_grant(self):
        reservation = self.budgets.reserve(self.lead,str(uuid4()),"model_calls")
        self.budgets.settle(self.lead,reservation["id"],str(uuid4()),certainty="unknown",started=True)
        self.bind_actors(); context = self.work.issue_initial_context(self.lead)
        self.error("actor_operation_unknown",lambda:self.create(context=context))
        self.assertEqual(self.rows("SELECT * FROM collab_work_items"),[])

    def test_stop_drains_work_and_continue_preserves_terminal_attempts_and_roots(self):
        complete = self.create(); incomplete = self.create()
        done_attempt = self.assign(complete)
        with self.store.transaction() as db:
            db.execute("UPDATE collab_work_attempts SET status='succeeded',outcome_json=? WHERE id=?",('{"certainty":"known"}',done_attempt["id"]))
            db.execute("UPDATE collab_work_items SET status='completed' WHERE id=?",(complete["id"],))
        unfinished_attempt = self.assign(incomplete)
        stopped = self.finish(); before = self.rows("SELECT * FROM collab_work_attempts ORDER BY id")
        self.assertEqual(self.work.get(self.run["id"],incomplete["id"])["status"],"cancelled")
        self.assertEqual(self.rows("SELECT status FROM collab_work_attempts WHERE id=?",(unfinished_attempt["id"],)),[{"status":"cancelled"}])
        self.run = self.runs.continue_run(self.run["id"],str(uuid4()),expected_revision=stopped["revision"])
        self.assertEqual(self.rows("SELECT * FROM collab_work_attempts ORDER BY id"),before)
        self.assertEqual(self.work.get(self.run["id"],complete["id"])["status"],"completed")
        self.assertEqual(self.work.get(self.run["id"],incomplete["id"])["status"],"pending")
        self.assertEqual(len(self.rows("SELECT * FROM collab_repair_roots")),2)

    def test_interrupt_preserves_unknown_and_fence_and_forbids_implicit_continue(self):
        item = self.create(); attempt = self.assign(item)
        interrupted = self.runs.mark_interrupted(self.run["id"],str(uuid4()),attempt_id=self.lead.attempt_id)
        self.assertEqual(self.work.get(self.run["id"],item["id"])["status"],"interrupted")
        old = self.rows("SELECT * FROM collab_work_attempts WHERE id=?",(attempt["id"],))[0]
        self.assertEqual((old["status"],json.loads(old["outcome_json"])["certainty"]),("interrupted","unknown"))
        self.error("resources_not_drained",lambda:self.runs.continue_run(self.run["id"],str(uuid4()),expected_revision=interrupted["revision"]))
        self.assertEqual(self.rows("SELECT * FROM collab_work_attempts WHERE id=?",(attempt["id"],))[0],old)
        self.assertEqual(len(self.rows("SELECT * FROM collab_activity")),1)

    def test_sql_fault_rolls_back_root_mapping_work_event_and_request(self):
        before = self.runs.get(self.run["id"])
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fixture_fail_work AFTER INSERT ON collab_work_items BEGIN SELECT RAISE(ABORT,'injected work failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError,"injected work failure"): self.create()
        for table in ["collab_work_items","collab_repair_roots","collab_work_roots"]: self.assertEqual(self.rows("SELECT * FROM "+table),[])
        self.assertEqual(self.rows("SELECT * FROM collab_requests WHERE action='create_work'"),[])
        self.assertEqual(self.rows("SELECT * FROM collab_events WHERE kind='work_created'"),[])
        self.assertEqual(self.runs.get(self.run["id"]),before)

    def test_attempt_update_cas_rolls_back_attempt_and_plan_sealing(self):
        item = self.create(); before = self.runs.get(self.run["id"])
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fixture_race_work AFTER INSERT ON collab_work_attempts BEGIN UPDATE collab_work_items SET revision=revision+1 WHERE id=NEW.work_id; END")
        self.error("stale_revision",lambda:self.assign(item))
        self.assertEqual(self.rows("SELECT * FROM collab_work_attempts"),[])
        self.assertEqual(self.rows("SELECT initial_open FROM collab_work_plan_gates"),[{"initial_open":1}])
        self.assertEqual(self.rows("SELECT active FROM collab_planning_contexts"),[{"active":1}])
        self.assertEqual(self.runs.get(self.run["id"]),before)

    def test_invalid_inputs_missing_parent_and_foreign_member_fail_closed(self):
        for value,code in [({"goal":"x","criteria":[]},"invalid_work_criteria"),
                           ({"goal":"x"*32001,"criteria":["y"]},"work_text_too_long"),
                           ({"goal":"x","criteria":["y"],"dependencies":"bad"},"invalid_dependencies"),
                           ({"goal":"x","criteria":["y"],"dependencies":[self.team["id"],self.team["id"]]},"duplicate_dependency")]:
            with self.subTest(code=code): self.error(code,lambda:self.work.create(self.context,str(uuid4()),value))
        self.error("missing_parent",lambda:self.work.issue_child_context(self.lead,[]))
        self.error("work_not_found",lambda:self.work.issue_child_context(self.lead,[str(uuid4())]))
        item = self.create()
        self.error("member_mismatch",lambda:self.assign(item,member=str(uuid4())))
        self.error("stale_revision",lambda:self.assign(item,revision=99))

    def test_foreign_run_work_and_workspace_scope_reject_even_valid_actor(self):
        original = self.runs.get(self.run["id"])
        stopped = self.finish()
        other_team = self.service.create_team(self.token,str(uuid4()),self.config)
        self.run = self.runs.prepare(other_team["id"],str(uuid4()),expected_revision=0)
        self.activate(); self.bind_actors(); self.context = self.work.issue_initial_context(self.lead)
        foreign_work = self.create(); foreign_member = self.peer.member_id
        self.finish()
        self.run = self.runs.continue_run(original["id"],str(uuid4()),expected_revision=stopped["revision"])
        self.activate(); self.bind_actors(); self.context = self.work.issue_initial_context(self.lead)
        item = self.create()
        self.error("work_mismatch",lambda:self.create(dependencies=[foreign_work["id"]]))
        self.error("work_mismatch",lambda:self.work.issue_child_context(self.lead,[foreign_work["id"]]))
        self.error("work_mismatch",lambda:self.work.get(self.run["id"],foreign_work["id"]))
        self.error("member_mismatch",lambda:self.assign(item,member=foreign_member))
        foreign = WorkRepository(RunRepository(self.store,Scope("admin","foreign-workspace")),self.auth.redactor)
        self.assertEqual(self.error("forbidden",lambda:foreign.get(self.run["id"],item["id"])).status,403)

    def test_stop_and_work_admission_are_serialized_by_run_revision(self):
        run = self.runs.get(self.run["id"]); barrier = Barrier(2)
        def action(kind):
            with CollaborationStore(self.path) as store:
                runs = RunRepository(store,self.scope,clock_ms=lambda:self.now)
                barrier.wait(timeout=5)
                try:
                    if kind=="stop": return runs.stop(run["id"],str(uuid4()),expected_revision=run["revision"])["status"]
                    return WorkRepository(runs,self.auth.redactor).create(self.context,str(uuid4()),{"goal":"race","criteria":["checked"]})["status"]
                except CollaborationError as exc: return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(action,["stop","create"]))
        self.assertIn(sorted(results),[sorted(["stopping","dispatch_closed"]),sorted(["ready","stale_revision"])])
        current = self.runs.get(run["id"])
        if current["status"]=="running": self.runs.stop(run["id"],str(uuid4()),expected_revision=current["revision"])
        self.error("dispatch_closed",lambda:self.create())

    def test_dependency_and_attempt_replays_redact_newly_known_secrets(self):
        item = self.create(goal="later-private")
        request = str(uuid4()); value = self.work.set_dependencies(self.lead,item["id"],request,[],expected_revision=0)
        self.auth.redactor.add("later-private")
        self.assertNotIn("later-private",self.work.set_dependencies(self.lead,item["id"],request,[],expected_revision=0)["goal"])
        # Initial attempt gets already-redacted data; its ledger is re-filtered too.
        second = self.create(goal="second-private")
        request = str(uuid4()); attempt = self.assign(second,request=request)
        self.auth.redactor.add("second-private")
        self.assertNotIn("second-private",self.assign(second,request=request)["inputs"]["goal"])
        self.assertEqual(self.rows("SELECT inputs_json FROM collab_work_attempts WHERE id=?",(attempt["id"],))[0]["inputs_json"],json.dumps(attempt["inputs"],sort_keys=True,separators=(",",":")))

    def copy_v5(self):
        old = Path(self.temporary.name)/"v5.sqlite"
        with closing(sqlite3.connect(old)): pass
        migrate_reviewed(old,expected_version=0,target_version=5)
        with closing(sqlite3.connect(old)) as dest:
            dest.execute("PRAGMA foreign_keys=ON"); dest.execute("BEGIN"); dest.execute("PRAGMA defer_foreign_keys=ON")
            names = [r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'collab_%' AND name!='collab_schema_migrations'")]
            with self.store.transaction(write=False) as source:
                for name in names:
                    columns = ','.join('"'+r[1]+'"' for r in dest.execute('PRAGMA table_info("'+name+'")'))
                    rows = source.execute('SELECT '+columns+' FROM "'+name+'"').fetchall()
                    dest.executemany('INSERT INTO "'+name+'" ('+columns+') VALUES ('+','.join('?' for _ in columns.split(','))+')',[tuple(r) for r in rows])
            dest.commit()
        return old

    def test_real_v5_data_migration_backfills_roots_and_keeps_backup(self):
        item = self.create(); self.assign(item)
        old = self.copy_v5()
        backup = old.with_name("v5-backup.sqlite"); migrate_reviewed(old,expected_version=5,backup=backup)
        with CollaborationStore(old) as store:
            with store.transaction(write=False) as db:
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
                self.assertEqual(db.execute("SELECT MAX(version) FROM collab_schema_migrations").fetchone()[0],SCHEMA_VERSION)
                self.assertEqual(dict(db.execute("SELECT * FROM collab_work_plan_gates").fetchone()),{"run_id":self.run["id"],"initial_open":0})
                self.assertEqual(dict(db.execute("SELECT * FROM collab_repair_roots").fetchone()),{"id":item["id"],"run_id":self.run["id"],"limit_total":3,"used":0,"reserved":0,"revision":0})
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute("SELECT MAX(version) FROM collab_schema_migrations").fetchone()[0],5)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='collab_repair_roots'").fetchone())
            self.assertEqual(db.execute("SELECT id FROM collab_work_items").fetchone()[0],item["id"])

    def test_migration_rejects_unaccounted_old_repairs_without_resetting_or_partial_schema(self):
        item = self.create(); self.assign(item); old = self.copy_v5()
        with closing(sqlite3.connect(old)) as db:
            db.execute("UPDATE collab_work_attempts SET status='failed',outcome_json=?",('{"certainty":"known"}',))
            db.execute("INSERT INTO collab_work_attempts(id,work_id,run_id,run_attempt_id,member_id,number,status,inputs_json,outcome_json) VALUES (?,?,?,?,?,2,'failed','{}',?)",
                       (str(uuid4()),item["id"],self.run["id"],self.lead.attempt_id,self.peer.member_id,'{"certainty":"known"}'))
            db.commit()
        backup = old.with_name("repair-audit-required-backup.sqlite")
        with self.assertRaisesRegex(sqlite3.IntegrityError,"unaccounted_repair_history"):
            migrate_reviewed(old,expected_version=5,backup=backup)
        for path in [old,backup]:
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT MAX(version) FROM collab_schema_migrations").fetchone()[0],5)
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='collab_repair_roots'").fetchone())
                self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_work_attempts").fetchone()[0],2)

    def test_continue_refuses_undrained_old_work_even_without_budget_or_turn(self):
        item = self.create(); self.assign(item)
        # Inconsistent persisted fixture; a run status alone is not drain proof.
        with self.store.transaction() as db: db.execute("UPDATE collab_runs SET status='failed' WHERE id=?",(self.run["id"],))
        run = self.runs.get(self.run["id"])
        self.error("resources_not_drained",lambda:self.runs.continue_run(run["id"],str(uuid4()),expected_revision=run["revision"]))
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM collab_run_attempts"),[{"n":1}])
        self.assertEqual(self.rows("SELECT status FROM collab_work_attempts"),[{"status":"queued"}])
