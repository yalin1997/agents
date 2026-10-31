"""Real SQLite cumulative-limit decisions; counters are seeded persistence fixtures."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from pi_deepagent.collaboration import repair_limits, storage
from pi_deepagent.collaboration.contracts import CollaborationError
from pi_deepagent.collaboration.policies import PolicyService
from pi_deepagent.collaboration.runs import RunRepository
from pi_deepagent.collaboration.storage import CollaborationStore, migrate_reviewed
from pi_deepagent.collaboration.work import WorkRepository
from pi_deepagent.web.auth import AuthError
from tests.collaboration.test_runs import RunFixture


class RepairLimitTests(RunFixture):
    def setUp(self):
        super().setUp(); self.activate()
        member = next(m for m in self.run["members"] if m["spec"]["orchestrator"])
        self.lead = self.runs.bind_member(self.run["id"],member["id"])
        self.work = WorkRepository(self.runs,self.auth.redactor)
        self.context = self.work.issue_initial_context(self.lead)
        self.root = self.work.create(self.context,str(uuid4()),{"goal":"Root A","criteria":["A"]})
        self.other = self.work.create(self.context,str(uuid4()),{"goal":"Root B","criteria":["B"]})
        self.policies = PolicyService(self.store,self.auth,self.workspace,clock_ms=lambda:self.now,ttl_seconds=2)
        # Nonzero data is synthetic, not measured model or repair execution.
        with self.store.transaction() as db:
            db.execute("UPDATE collab_repair_roots SET used=2,reserved=1,revision=revision+1 WHERE id=?",(self.root["id"],))
            db.execute("UPDATE collab_budget_totals SET used=1")
            db.execute("UPDATE collab_runs SET wall_used_ms=1000")

    def rows(self,sql,params=()):
        with self.store.transaction(write=False) as db: return [dict(row) for row in db.execute(sql,params)]

    def current(self,root=None):
        return self.policies.list_repair_limits(self.token,self.run["id"],root or self.root["id"])["current"]

    def payload(self,**changes):
        run = self.runs.get(self.run["id"])
        return {"kind":"budget_extension","repair_root_id":self.root["id"],"work_id":self.root["id"],
                "old":{"repair_attempts":self.current()["limit_total"]},"new":{"repair_attempts":5},
                "policy_version":run["policy_version"],"expected_revision":run["revision"],"reason":"Reviewed cumulative repair total"}|changes

    def request(self,**changes):
        return self.policies.create_request(self.token,self.run["id"],str(uuid4()),self.payload(**changes))

    def decide(self,request,decision="approve",identifier=None,service=None):
        return (service or self.policies).decide(self.token,self.run["id"],identifier or str(uuid4()),
            {"approval_id":request["id"],"decision":decision,"expected_revision":request["revision"],"reason":"Admin approved exact root only"})

    def error(self,code,call):
        with self.assertRaises(CollaborationError) as caught: call()
        self.assertEqual(caught.exception.code,code)

    def test_extension_keeps_nonzero_usage_reservations_global_defaults_and_other_roots(self):
        before = self.runs.get(self.run["id"]); other = self.current(self.other["id"])
        request = self.request(); result = self.decide(request); root = self.current()
        self.assertEqual((request["target_type"],request["target_id"]),("repair_root",self.root["id"]))
        self.assertEqual((root["limit_total"],root["used"],root["reserved"],root["limit_version"],root["revision"]),(5,2,1,2,2))
        self.assertEqual(result["repair_root_id"],self.root["id"])
        self.assertEqual(self.current(self.other["id"]),other)
        after = self.runs.get(self.run["id"])
        for key in ("policy","members","config","attempt","budgets","wall_used_ms"): self.assertEqual(after[key],before[key])
        self.assertEqual(after["policy"]["limits"]["repair_attempts"],3)
        self.assertEqual(after["status"],"running")

    def test_child_and_multi_root_work_inherit_extended_root_without_extending_others(self):
        child = self.work.create(self.work.issue_child_context(self.lead,[self.root["id"],self.other["id"]]),str(uuid4()),{"goal":"Combined","criteria":["all"]})
        request = self.request(work_id=child["id"]); self.decide(request)
        view = self.work.get(self.run["id"],child["id"])
        roots = {row["id"]:row for row in view["roots"]}
        self.assertEqual((roots[self.root["id"]]["limit_total"],roots[self.other["id"]]["limit_total"]),(5,3))
        self.assertEqual(roots[self.root["id"]]["used"],2)
        self.error("repair_root_mismatch",lambda:self.request(repair_root_id=child["id"],new={"repair_attempts":7}))

    def test_root_target_is_required_and_cannot_mix_run_totals_or_other_approval_kinds(self):
        value = self.payload(); del value["repair_root_id"]
        self.error("unsupported_budget_target",lambda:self.policies.create_request(self.token,self.run["id"],str(uuid4()),value))
        for changes,code in [({"old":{"messages":3},"new":{"messages":5}},"unsupported_budget_target"),
                             ({"old":{"repair_attempts":3,"messages":3},"new":{"repair_attempts":5,"messages":5}},"unsupported_budget_target"),
                             ({"kind":"capability"},"invalid_fields"),({"repair_root_id":None},"invalid_id"),
                             ({"member_id":self.lead.member_id},"invalid_fields")]:
            with self.subTest(code=code): self.error(code,lambda:self.request(**changes))
        self.assertEqual(self.rows("SELECT * FROM collab_approval_requests"),[])

    def test_exact_old_total_strict_integers_and_increases(self):
        for changes,code in [({"old":{"repair_attempts":2}},"budget_baseline_changed"),
                             ({"new":{"repair_attempts":3}},"budget_increase_required"),
                             ({"new":{"repair_attempts":1}},"budget_increase_required"),
                             ({"new":{"repair_attempts":True}},"invalid_integer"),
                             ({"old":{"repair_attempts":-1}},"invalid_integer")]:
            with self.subTest(code=code): self.error(code,lambda:self.request(**changes))
        self.assertEqual(self.current()["limit_total"],3)

    def test_zero_initial_total_can_be_explicitly_extended_without_changing_other_defaults(self):
        # A separate valid run with zero configured repairs, not a counter reset.
        with self.store.transaction() as db: db.execute("UPDATE collab_repair_roots SET reserved=0")
        self.finish()
        config = self.config | {"policy":{"limits":{"repair_attempts":0}}}
        team = self.service.create_team(self.token,str(uuid4()),config)
        self.run = self.runs.prepare(team["id"],str(uuid4()),expected_revision=0); self.activate()
        lead_id = next(m["id"] for m in self.run["members"] if m["spec"]["orchestrator"])
        actor = self.runs.bind_member(self.run["id"],lead_id)
        self.root = self.work.create(self.work.issue_initial_context(actor),str(uuid4()),{"goal":"Disabled by default","criteria":["one"]})
        request = self.request(new={"repair_attempts":1}); self.decide(request)
        self.assertEqual(self.current()["limit_total"],1)
        self.assertEqual(self.runs.get(self.run["id"])["policy"]["limits"]["repair_attempts"],0)

    def test_request_and_decision_replay_are_stable_after_further_extension(self):
        value = self.payload(); identifier = str(uuid4())
        request = self.policies.create_request(self.token,self.run["id"],identifier,value)
        decision_id = str(uuid4()); first = self.decide(request,identifier=decision_id)
        self.decide(self.request(new={"repair_attempts":7}))
        self.assertEqual(self.policies.create_request(self.token,self.run["id"],identifier,value),request)
        self.assertEqual(self.decide(request,identifier=decision_id),first)
        self.assertEqual(self.current()["limit_total"],7)
        self.assertEqual(len(self.policies.list_repair_limits(self.token,self.run["id"],self.root["id"])["items"]),3)
        self.error("request_conflict",lambda:self.policies.create_request(self.token,self.run["id"],identifier,value|{"new":{"repair_attempts":8}}))

    def test_deny_expire_stop_and_late_decision_never_add_capacity(self):
        first = self.request(); self.decide(first,"deny")
        second = self.request(); self.now += 2000; self.error("approval_expired",lambda:self.decide(second))
        third = self.request(); run = self.runs.get(self.run["id"])
        self.runs.stop(run["id"],str(uuid4()),expected_revision=run["revision"])
        self.error("approval_closed",lambda:self.decide(third))
        self.assertEqual((self.current()["limit_total"],self.current()["reserved"]),(3,1))

    def test_interrupted_extension_does_not_resume_or_release_unknown_repair_reservation(self):
        interrupted = self.runs.mark_interrupted(self.run["id"],str(uuid4()),attempt_id=self.lead.attempt_id)
        attempt = interrupted["attempt"]
        self.decide(self.request())
        after = self.runs.get(self.run["id"])
        self.assertEqual((after["status"],after["attempt"]),("interrupted",attempt))
        self.assertEqual(self.current()["reserved"],1)
        self.error("resources_not_drained",lambda:self.runs.continue_run(after["id"],str(uuid4()),expected_revision=after["revision"]))
        self.assertEqual(len(self.rows("SELECT * FROM collab_activity")),1)

    def test_stop_completion_retains_fence_until_repair_reservations_drained(self):
        run = self.runs.get(self.run["id"])
        self.runs.stop(run["id"],str(uuid4()),expected_revision=run["revision"])
        self.error("resources_not_drained",lambda:self.runs.finish_stopping(run["id"],str(uuid4()),attempt_id=self.lead.attempt_id))
        self.assertEqual(self.runs.get(run["id"])["status"],"stopping")
        self.assertEqual(len(self.rows("SELECT * FROM collab_activity")),1)

    def test_foreign_root_unrelated_work_and_foreign_workspace_reject(self):
        self.error("repair_root_mismatch",lambda:self.request(repair_root_id=str(uuid4())))
        self.error("repair_root_work_mismatch",lambda:self.request(work_id=self.other["id"]))
        self.error("repair_root_mismatch",lambda:self.policies.list_repair_limits(self.token,self.run["id"],str(uuid4())))
        directory = Path(self.temporary.name)/"foreign"; directory.mkdir()
        service = PolicyService(self.store,self.auth,directory)
        self.error("forbidden",lambda:service.list_repair_limits(self.token,self.run["id"],self.root["id"]))

    def test_history_auth_matrix_and_revoked_replay(self):
        value = self.payload(); identifier = str(uuid4())
        self.policies.create_request(self.token,self.run["id"],identifier,value)
        for token in (None,"invalid"):
            with self.assertRaises(AuthError) as caught: self.policies.list_repair_limits(token,self.run["id"],self.root["id"])
            self.assertEqual(caught.exception.status,401)
        key = self.auth._digest(self.token); original = self.auth.sessions[key]
        self.auth.sessions[key] = replace(original,role="reader")
        self.error("forbidden",lambda:self.policies.list_repair_limits(self.token,self.run["id"],self.root["id"]))
        self.auth.sessions[key] = replace(original,expires_at=0)
        with self.assertRaises(AuthError): self.policies.list_repair_limits(self.token,self.run["id"],self.root["id"])
        self.auth.sessions[key] = original
        self.assertEqual(self.current()["limit_version"],1)
        self.auth.logout(self.token)
        with self.assertRaises(AuthError): self.policies.create_request(self.token,self.run["id"],identifier,value)

    def test_existing_root_from_another_run_is_not_a_local_budget_target(self):
        old_root = self.root["id"]
        with self.store.transaction() as db: db.execute("UPDATE collab_repair_roots SET reserved=0")
        self.finish()
        team = self.service.create_team(self.token,str(uuid4()),self.config)
        self.run = self.runs.prepare(team["id"],str(uuid4()),expected_revision=0); self.activate()
        run = self.runs.get(self.run["id"])
        self.error("repair_root_mismatch",lambda:self.policies.list_repair_limits(self.token,run["id"],old_root))
        value = {"kind":"budget_extension","repair_root_id":old_root,"old":{"repair_attempts":3},"new":{"repair_attempts":5},
                 "policy_version":run["policy_version"],"expected_revision":run["revision"],"reason":"Foreign existing root must fail"}
        self.error("repair_root_mismatch",lambda:self.policies.create_request(self.token,run["id"],str(uuid4()),value))

    def test_parallel_approve_deny_has_one_version_and_immutable_result(self):
        request = self.request(); barrier = Barrier(2)
        def race(decision):
            with CollaborationStore(self.path) as store:
                service = PolicyService(store,self.auth,self.workspace,clock_ms=lambda:self.now)
                barrier.wait(timeout=5)
                try: return self.decide(request,decision,service=service)["status"]
                except CollaborationError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(race,["approve","deny"]))
        self.assertEqual(results.count("approval_closed"),1)
        winner = next(x for x in results if x!="approval_closed")
        self.assertEqual(self.current()["limit_total"],5 if winner=="approved" else 3)
        self.assertEqual(len(self.rows("SELECT * FROM collab_approval_decisions")),1)

    def test_other_policy_change_supersedes_pending_root_extension(self):
        first = self.request(); run = self.runs.get(self.run["id"])
        request = self.policies.create_request(self.token,run["id"],str(uuid4()),{"kind":"budget_extension","old":{"messages":3},"new":{"messages":7},
            "reason":"separate total","policy_version":run["policy_version"],"expected_revision":run["revision"]})
        self.decide(request); self.error("approval_stale",lambda:self.decide(first))
        self.assertEqual(self.current()["limit_total"],3)

    def test_root_and_run_cas_faults_roll_back_cache_history_policy_and_ledger(self):
        for table,column in [("collab_repair_roots","root_id"),("collab_runs","run_id")]:
            request = self.request(); before = self.runs.get(self.run["id"]); root = self.current()
            with self.store.transaction() as db:
                db.execute("CREATE TRIGGER fixture_limit_race AFTER INSERT ON collab_repair_limit_versions WHEN NEW.version>1 BEGIN UPDATE "+table+" SET revision=revision+1 WHERE id=NEW."+column+"; END")
            self.error("stale_revision",lambda:self.decide(request))
            self.assertEqual(self.current(),root)
            self.assertEqual(self.runs.get(self.run["id"]),before)
            self.assertEqual(len(self.rows("SELECT * FROM collab_policies")),1)
            self.assertEqual(self.rows("SELECT * FROM collab_requests WHERE action='decide_budget_extension'"),[])
            self.assertEqual(self.rows("SELECT * FROM collab_events WHERE kind='repair_limit_extended'"),[])
            with self.store.transaction() as db: db.execute("DROP TRIGGER fixture_limit_race")

    def test_inconsistent_overdrawn_data_cannot_be_papered_over_with_insufficient_extension(self):
        with self.store.transaction() as db: db.execute("UPDATE collab_repair_roots SET used=5,revision=revision+1 WHERE id=?",(self.root["id"],))
        request = self.request(new={"repair_attempts":5}); before = self.current()
        self.error("repair_extension_insufficient",lambda:self.decide(request))
        self.assertEqual(self.current(),before)
        self.assertEqual(self.runs.get(self.run["id"])["policy_version"],1)

    def test_direct_cache_or_history_rewrite_or_unapproved_limit_is_rejected(self):
        self.decide(self.request())
        for query in ["UPDATE collab_repair_roots SET limit_total=99","UPDATE collab_repair_limit_versions SET limit_total=99","DELETE FROM collab_repair_limit_versions"]:
            with self.subTest(query=query),self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as db: db.execute(query)
        self.assertEqual(self.current()["limit_total"],5)

    def test_history_pagination_and_reason_secrets_keep_exact_targets(self):
        self.auth.redactor.add("private-reason")
        first = self.request(reason="private-reason"); self.decide(first)
        self.decide(self.request(new={"repair_attempts":7}))
        page = self.policies.list_repair_limits(self.token,self.run["id"],self.root["id"],limit=1)
        second = self.policies.list_repair_limits(self.token,self.run["id"],self.root["id"],after_version=page["next_cursor"],limit=1)
        self.assertEqual((page["items"][0]["version"],second["items"][0]["version"]),(1,2))
        self.assertTrue(page["has_more"])
        self.assertEqual(self.policies.list_requests(self.token,self.run["id"])["items"][0]["reason"],"[REDACTED]")
        self.error("invalid_integer",lambda:self.policies.list_repair_limits(self.token,self.run["id"],self.root["id"],limit=0))

    def copy_v8(self):
        old = Path(self.temporary.name)/"v8.sqlite"
        with closing(sqlite3.connect(old)): pass
        migrate_reviewed(old,expected_version=0,target_version=8)
        with closing(sqlite3.connect(old)) as dest:
            dest.execute("PRAGMA foreign_keys=ON"); dest.execute("BEGIN"); dest.execute("PRAGMA defer_foreign_keys=ON")
            triggers = [row[0] for row in dest.execute("SELECT sql FROM sqlite_master WHERE name IN ('collab_initial_work_root','collab_initial_run_criteria','collab_initial_work_criteria')")]
            for name in ['collab_initial_work_root','collab_initial_run_criteria','collab_initial_work_criteria']: dest.execute('DROP TRIGGER '+name)
            names = [r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'collab_%' AND name!='collab_schema_migrations'")]
            with self.store.transaction(write=False) as source:
                for name in names:
                    cols = [r[1] for r in dest.execute('PRAGMA table_info("'+name+'")')]; quoted = ','.join('"'+c+'"' for c in cols)
                    rows = source.execute('SELECT '+quoted+' FROM "'+name+'"').fetchall()
                    dest.executemany('INSERT INTO "'+name+'" ('+quoted+') VALUES ('+','.join('?' for _ in cols)+')',[tuple(r) for r in rows])
            for sql in triggers: dest.execute(sql)
            dest.commit()
        return old

    def test_v8_backfill_keeps_nonzero_counts_and_snapshot_with_backup(self):
        old = self.copy_v8(); backup = old.with_name("v8-backup.sqlite")
        migrate_reviewed(old,expected_version=8,backup=backup)
        with CollaborationStore(old) as store:
            service = PolicyService(store,self.auth,self.workspace)
            value = service.list_repair_limits(self.token,self.run["id"],self.root["id"])
            self.assertEqual((value["current"]["limit_total"],value["current"]["used"],value["current"]["reserved"]),(3,2,1))
            self.assertEqual(value["items"][0]["source"],"migration")
            with store.transaction(write=False) as db: self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute("SELECT MAX(version) FROM collab_schema_migrations").fetchone()[0],8)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='collab_repair_limit_versions'").fetchone())

    def test_v9_failure_rolls_back_history_backfill_and_preserves_v8_backup(self):
        old = self.copy_v8(); backup = old.with_name("v8-failure-backup.sqlite"); original = storage.MIGRATIONS[8].read_bytes()
        class BadMigration:
            def read_bytes(self): return original+b'\nSELECT * FROM fixture_repair_limit_missing_table;\n'
        with patch.object(storage,"MIGRATIONS",storage.MIGRATIONS[:8]+(BadMigration(),)+storage.MIGRATIONS[9:]):
            with self.assertRaisesRegex(sqlite3.OperationalError,"fixture_repair_limit_missing_table"):
                migrate_reviewed(old,expected_version=8,backup=backup)
        for path in [old,backup]:
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT MAX(version) FROM collab_schema_migrations").fetchone()[0],8)
                self.assertEqual(db.execute("SELECT used,reserved FROM collab_repair_roots WHERE id=?",(self.root["id"],)).fetchone(),(2,1))
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='collab_repair_limit_versions'").fetchone())
