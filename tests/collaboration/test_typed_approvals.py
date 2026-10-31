"""Typed policy persistence; runtime support is fixture metadata, not M0 proof."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from pi_deepagent.collaboration import storage
from pi_deepagent.collaboration.contracts import CollaborationError
from pi_deepagent.collaboration.policies import PolicyService, CAPABILITIES
from pi_deepagent.collaboration.runs import RunRepository
from pi_deepagent.collaboration.storage import CollaborationStore, migrate_reviewed, Scope
from pi_deepagent.collaboration.work import WorkRepository
from pi_deepagent.web.auth import AuthError
from tests.collaboration.test_runs import RunFixture


class TypedApprovalTests(RunFixture):
    def setUp(self):
        super().setUp(); self.activate()
        self.work = WorkRepository(self.runs,self.auth.redactor)
        members = self.runs.get(self.run["id"])["members"]
        self.lead_id = next(m["id"] for m in members if m["spec"]["orchestrator"])
        self.member_id = next(m["id"] for m in members if not m["spec"]["orchestrator"])
        self.lead = self.runs.bind_member(self.run["id"],self.lead_id)
        context = self.work.issue_initial_context(self.lead)
        self.item = self.work.create(context,str(uuid4()),{"goal":"Implement requirement","criteria":["verified"]})
        self.other = self.work.create(context,str(uuid4()),{"goal":"Independent requirement","criteria":["verified"]})
        self.policies = self.policy_service()

    def policy_service(self,store=None,**kw):
        return PolicyService(store or self.store,self.auth,self.workspace,clock_ms=lambda:self.now,ttl_seconds=2,
                             available_capabilities=kw.pop("available_capabilities",CAPABILITIES),**kw)

    def rows(self,query,params=()):
        with self.store.transaction(write=False) as db: return [dict(row) for row in db.execute(query,params)]

    def payload(self,kind="capability",**changes):
        run = self.runs.get(self.run["id"])
        value = {"kind":kind,"old":{"execute":False},"new":{"execute":True},"reason":"Explicit isolated policy ceiling",
                 "policy_version":run["policy_version"],"expected_revision":run["revision"]}
        if kind=="responsibility_exception":
            value.update(old={"paths":[]},new={"paths":["src"]},work_id=self.item["id"],member_id=self.member_id)
        elif kind=="budget_extension": value.update(old={"messages":run["policy"]["limits"]["messages"]},new={"messages":7})
        return value | changes

    def request(self,kind="capability",**changes):
        return self.policies.create_request(self.token,self.run["id"],str(uuid4()),self.payload(kind,**changes))

    def decide(self,request,decision="approve",request_id=None,service=None):
        return (service or self.policies).decide(self.token,self.run["id"],request_id or str(uuid4()),
            {"approval_id":request["id"],"decision":decision,"expected_revision":request["revision"],"reason":"Reviewed exact target"})

    def error(self,code,call):
        with self.assertRaises(CollaborationError) as raised: call()
        self.assertEqual(raised.exception.code,code)
        return raised.exception

    def bind(self,member=None): return self.runs.bind_member(self.run["id"],member or self.member_id)

    def test_capability_ceiling_does_not_bundle_network_install_or_mutate_snapshot(self):
        before = self.runs.get(self.run["id"]); request = self.request(); self.decide(request)
        after = self.runs.get(self.run["id"])
        self.assertEqual(after["members"],before["members"])
        self.assertEqual(after["attempt"],before["attempt"])
        self.assertEqual(after["budgets"],before["budgets"])
        self.assertEqual((after["policy"]["execute"],after["policy"]["network"],after["policy"]["dependency_install"],after["policy"]["publish"]),(True,False,False,False))
        self.assertEqual(after["policy_version"],2)
        self.assertEqual(self.policies.list_policies(self.token,self.run["id"])["items"][1]["source"],"capability")
        self.error("stale_policy",lambda:self.budgets.reserve(self.lead,str(uuid4()),"model_calls"))

    def test_default_unavailable_capability_and_decision_time_recheck(self):
        default = PolicyService(self.store,self.auth,self.workspace,clock_ms=lambda:self.now)
        self.error("capability_unavailable",lambda:default.create_request(self.token,self.run["id"],str(uuid4()),self.payload()))
        request = self.request(); before = self.runs.get(self.run["id"])
        self.error("capability_unavailable",lambda:self.decide(request,service=default))
        self.assertEqual(self.runs.get(self.run["id"]),before)
        self.assertEqual(self.rows("SELECT status FROM collab_approval_requests"),[{"status":"pending"}])
        self.assertEqual(self.rows("SELECT * FROM collab_approval_decisions"),[])
        self.assertEqual(self.decide(request,"deny",service=default)["status"],"denied")

    def test_strict_capability_booleans_targets_no_change_or_implicit_revocation(self):
        for changes,code in [({"new":{"execute":1}},"invalid_capability"),({"new":{"execute":"yes"}},"invalid_capability"),
                             ({"old":{"host_command":False},"new":{"host_command":True}},"unsupported_capability_target"),
                             ({"new":{"execute":False}},"capability_increase_required"),
                             ({"old":{"execute":True},"new":{"execute":False}},"revocation_required"),
                             ({"member_id":self.member_id},"invalid_fields"),({"target_id":"host"},"invalid_fields")]:
            with self.subTest(code=code): self.error(code,lambda:self.request(**changes))
        self.assertEqual(self.rows("SELECT * FROM collab_approval_requests"),[])

    def test_request_and_decision_idempotence_preserve_exact_typed_results(self):
        request_id = str(uuid4()); value = self.payload()
        request = self.policies.create_request(self.token,self.run["id"],request_id,value)
        self.assertEqual(self.policies.create_request(self.token,self.run["id"],request_id,value),request)
        self.error("request_conflict",lambda:self.policies.create_request(self.token,self.run["id"],request_id,value|{"reason":"different"}))
        request_id = str(uuid4()); result = self.decide(request,request_id=request_id)
        self.assertEqual(self.decide(request,request_id=request_id),result)
        self.assertEqual(len(self.rows("SELECT * FROM collab_policies")),2)
        self.assertEqual(len(self.rows("SELECT * FROM collab_approval_decisions")),1)

    def test_responsibility_exception_bound_to_work_member_and_attempt(self):
        before = self.runs.get(self.run["id"]); binding = self.bind()
        self.assertFalse(self.policies.covers_responsibility(binding,self.item["id"],["src/main.py"]))
        request = self.request("responsibility_exception"); self.decide(request)
        self.assertEqual((request["target_type"],request["target_id"],request["work_id"]),("work_member",self.member_id,self.item["id"]))
        binding = self.bind()
        self.assertTrue(self.policies.covers_responsibility(binding,self.item["id"],["src/main.py","src/pkg/a.py"]))
        self.assertFalse(self.policies.covers_responsibility(binding,self.item["id"],["src2/main.py"]))
        self.assertFalse(self.policies.covers_responsibility(binding,self.other["id"],["src/main.py"]))
        self.assertFalse(self.policies.covers_responsibility(self.bind(self.lead_id),self.item["id"],["src/main.py"]))
        self.assertEqual(self.runs.get(self.run["id"])["members"],before["members"])
        self.assertEqual(self.runs.get(self.run["id"])["policy"],before["policy"])
        self.assertEqual(len(self.rows("SELECT * FROM collab_responsibility_versions")),1)

    def test_responsibility_paths_are_literal_bounded_and_preserve_old_coverage(self):
        first = self.request("responsibility_exception",new={"paths":["src","src/nested"]}); self.decide(first)
        self.assertEqual(first["new"],{"paths":["src"]})
        for changes,code in [({"old":{"paths":[]}},"approval_baseline_changed"),
                             ({"new":{"paths":["tests"]}},"responsibility_extension_required"),
                             ({"new":{"paths":["src"]}},"responsibility_extension_required"),
                             ({"new":{"paths":["../host"]}},"invalid_path"),
                             ({"new":{"paths":["C:/host"]}},"invalid_path"),
                             ({"new":{"paths":["src","src"]}},"duplicate_responsibility_path"),
                             ({"new":{"paths":"src"}},"invalid_responsibility_paths")]:
            with self.subTest(code=code): self.error(code,lambda:self.request("responsibility_exception",**({"old":{"paths":["src"]}}|changes)))
        second = self.request("responsibility_exception",old={"paths":["src"]},new={"paths":["src","tests"]}); self.decide(second)
        self.assertTrue(self.policies.covers_responsibility(self.bind(),self.item["id"],["src/a","tests/a"]))

    def test_responsibility_missing_or_foreign_targets_rejected(self):
        for changes,code in [({"member_id":None},"responsibility_target_required"),({"work_id":None},"responsibility_target_required"),
                             ({"member_id":str(uuid4())},"member_mismatch"),({"work_id":str(uuid4())},"work_mismatch")]:
            with self.subTest(code=code): self.error(code,lambda:self.request("responsibility_exception",**changes))
        self.assertEqual(self.rows("SELECT * FROM collab_responsibility_versions"),[])

    def test_old_attempt_exception_is_not_reused_on_continue(self):
        self.decide(self.request("responsibility_exception")); old = self.rows("SELECT * FROM collab_responsibility_versions")
        stopped = self.finish()
        self.run = self.runs.continue_run(self.run["id"],str(uuid4()),expected_revision=stopped["revision"])
        self.activate()
        self.assertFalse(self.policies.covers_responsibility(self.bind(),self.item["id"],["src/a"]))
        self.assertEqual(self.rows("SELECT * FROM collab_responsibility_versions"),old)

    def test_pending_exception_for_completed_work_is_superseded_without_policy(self):
        request = self.request("responsibility_exception")
        with self.store.transaction() as db: db.execute("UPDATE collab_work_items SET status='completed' WHERE id=?",(self.item["id"],))
        self.error("approval_stale",lambda:self.decide(request))
        self.assertEqual(self.rows("SELECT status FROM collab_approval_requests"),[{"status":"superseded"}])
        self.assertEqual(self.rows("SELECT * FROM collab_responsibility_versions"),[])
        self.assertEqual(self.runs.get(self.run["id"])["policy_version"],1)

    def test_capability_and_responsibility_approvals_cannot_rebase_other_pending_request(self):
        first = self.request(); second = self.request("responsibility_exception")
        self.decide(first)
        self.error("approval_stale",lambda:self.decide(second))
        self.assertEqual(self.rows("SELECT * FROM collab_responsibility_versions"),[])

    def test_denial_and_expiration_do_not_grant_or_reset_anything(self):
        request = self.request("responsibility_exception"); self.decide(request,"deny")
        self.assertEqual(self.runs.get(self.run["id"])["policy_version"],1)
        self.assertFalse(self.policies.covers_responsibility(self.bind(),self.item["id"],["src/a"]))
        expired = self.request(); self.now += 2000
        self.error("approval_expired",lambda:self.decide(expired))
        self.assertEqual(self.rows("SELECT status FROM collab_approval_requests ORDER BY sequence"),[{"status":"denied"},{"status":"expired"}])
        self.assertEqual(self.runs.get(self.run["id"])["policy"]["execute"],False)

    def test_stop_and_late_decision_cannot_publish_policy_or_exception(self):
        request = self.request("responsibility_exception"); run = self.runs.get(self.run["id"])
        self.runs.stop(run["id"],str(uuid4()),expected_revision=run["revision"])
        self.error("approval_closed",lambda:self.decide(request))
        self.error("dispatch_closed",lambda:self.policies.covers_responsibility(self.bind(),self.item["id"],["src/a"]))
        self.assertEqual(self.rows("SELECT * FROM collab_responsibility_versions"),[])

    def test_authentication_operation_and_workspace_before_create_decide_replay_and_list(self):
        request = self.request("responsibility_exception")
        for token in [None,"invalid"]:
            for call in [lambda:self.policies.create_request(token,self.run["id"],str(uuid4()),self.payload()),
                         lambda:self.policies.decide(token,self.run["id"],str(uuid4()),{"approval_id":request["id"]}),
                         lambda:self.policies.list_requests(token,self.run["id"])]:
                with self.subTest(token=token),self.assertRaises(AuthError) as raised: call()
                self.assertEqual(raised.exception.status,401)
        denied = self.policy_service(allowed_actions=frozenset({"read"}))
        self.error("forbidden",lambda:self.decide(request,service=denied))
        foreign = Path(self.temporary.name)/"foreign"; foreign.mkdir()
        service = PolicyService(self.store,self.auth,foreign,available_capabilities=CAPABILITIES)
        self.error("forbidden",lambda:service.list_requests(self.token,self.run["id"]))
        self.auth.logout(self.token)
        with self.assertRaises(AuthError): self.decide(request)

    def test_invalid_member_binding_and_unknown_actor_cannot_use_submission_rule(self):
        self.decide(self.request("responsibility_exception")); binding = self.bind()
        self.error("invalid_binding",lambda:self.policies.covers_responsibility({},self.item["id"],[]))
        self.error("invalid_binding",lambda:self.policies.covers_responsibility(replace(binding,grant_id=str(uuid4())),self.item["id"],["src/a"]))
        reservation = self.budgets.reserve(binding,str(uuid4()),"model_calls")
        self.budgets.settle(binding,reservation["id"],str(uuid4()),certainty="unknown",started=True)
        self.error("actor_operation_unknown",lambda:self.policies.covers_responsibility(self.bind(),self.item["id"],["src/a"]))

    def test_logout_revokes_admin_decisions_but_not_preauthorized_background_binding(self):
        self.decide(self.request("responsibility_exception")); binding = self.bind(); self.auth.logout(self.token)
        self.assertTrue(self.policies.covers_responsibility(binding,self.item["id"],["src/a"]))
        with self.assertRaises(AuthError): self.policies.list_requests(self.token,self.run["id"])

    def test_secret_reasons_filter_on_immutable_replay_and_history(self):
        value = self.payload(reason="later-secret"); request_id = str(uuid4())
        request = self.policies.create_request(self.token,self.run["id"],request_id,value)
        self.auth.redactor.add("later-secret")
        self.assertEqual(self.policies.create_request(self.token,self.run["id"],request_id,value)["reason"],"[REDACTED]")
        self.assertEqual(self.policies.list_requests(self.token,self.run["id"])["items"][0]["reason"],"[REDACTED]")
        self.assertEqual(self.rows("SELECT reason FROM collab_approval_requests")[0]["reason"],"later-secret")

    def test_request_replay_survives_support_removal_or_newly_known_path_secret(self):
        value = self.payload(); identifier = str(uuid4())
        request = self.policies.create_request(self.token,self.run["id"],identifier,value)
        unavailable = self.policy_service(available_capabilities=frozenset())
        self.assertEqual(unavailable.create_request(self.token,self.run["id"],identifier,value),request)
        value = self.payload("responsibility_exception"); identifier = str(uuid4())
        self.policies.create_request(self.token,self.run["id"],identifier,value)
        self.auth.redactor.add("src")
        replay = self.policies.create_request(self.token,self.run["id"],identifier,value)
        self.assertEqual(replay["new"],{"paths":["[REDACTED]"]})
        self.error("sensitive_responsibility_path",lambda:self.request("responsibility_exception"))

    def test_parallel_responsibility_decisions_have_one_version_and_result(self):
        request = self.request("responsibility_exception"); barrier = Barrier(2)
        def race(decision):
            with CollaborationStore(self.path) as store:
                service = self.policy_service(store)
                barrier.wait(timeout=5)
                try: return self.decide(request,decision,service=service)["status"]
                except CollaborationError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(race,["approve","deny"]))
        self.assertEqual(results.count("approval_closed"),1)
        winner = next(r for r in results if r!="approval_closed")
        self.assertEqual(len(self.rows("SELECT * FROM collab_responsibility_versions")),1 if winner=="approved" else 0)
        self.assertEqual(len(self.rows("SELECT * FROM collab_approval_decisions")),1)

    def test_policy_cas_rolls_back_exception_and_approval_together(self):
        request = self.request("responsibility_exception"); before = self.runs.get(self.run["id"])
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fixture_policy_race AFTER INSERT ON collab_responsibility_versions BEGIN UPDATE collab_runs SET revision=revision+1 WHERE id=NEW.run_id; END")
        self.error("stale_revision",lambda:self.decide(request))
        self.assertEqual(self.runs.get(self.run["id"]),before)
        self.assertEqual(self.rows("SELECT * FROM collab_responsibility_versions"),[])
        self.assertEqual(self.rows("SELECT status FROM collab_approval_requests"),[{"status":"pending"}])

    def test_responsibility_sql_history_and_identity_cannot_be_changed(self):
        request = self.request("responsibility_exception"); self.decide(request)
        for query in ["UPDATE collab_responsibility_versions SET paths_json='[\".\"]'","DELETE FROM collab_responsibility_versions",
                      "UPDATE collab_approval_requests SET target_id='host'","UPDATE collab_approval_requests SET member_id=NULL"]:
            with self.subTest(query=query),self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as db: db.execute(query)
        self.assertTrue(self.policies.covers_responsibility(self.bind(),self.item["id"],["src/a"]))

    def copy_v6(self):
        old = Path(self.temporary.name)/"v6.sqlite"
        with closing(sqlite3.connect(old)): pass
        migrate_reviewed(old,expected_version=0,target_version=6)
        with closing(sqlite3.connect(old)) as dest:
            dest.execute("PRAGMA foreign_keys=ON"); dest.execute("BEGIN"); dest.execute("PRAGMA defer_foreign_keys=ON")
            # Copy the already materialized rows exactly, without firing the
            # ordinary creation trigger a second time. Restore it before commit.
            root_trigger = dest.execute("SELECT sql FROM sqlite_master WHERE name='collab_initial_work_root'").fetchone()[0]
            dest.execute("DROP TRIGGER collab_initial_work_root")
            names = [r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'collab_%' AND name!='collab_schema_migrations'")]
            with self.store.transaction(write=False) as source:
                for name in names:
                    cols = [r[1] for r in dest.execute('PRAGMA table_info("'+name+'")')]
                    quoted = ','.join('"'+c+'"' for c in cols)
                    rows = source.execute('SELECT '+quoted+' FROM "'+name+'"').fetchall()
                    dest.executemany('INSERT INTO "'+name+'" ('+quoted+') VALUES ('+','.join('?' for _ in cols)+')',[tuple(r) for r in rows])
            dest.execute("UPDATE sqlite_sequence SET seq=700 WHERE name='collab_approval_requests'")
            dest.execute(root_trigger)
            dest.commit()
        return old

    def test_real_v6_rebuild_keeps_pending_approved_policy_ledger_sequence_and_backup(self):
        first = self.request("budget_extension"); self.decide(first)
        pending = self.request("budget_extension",new={"messages":9})
        old = self.copy_v6(); backup = old.with_name("v6-backup.sqlite")
        migrate_reviewed(old,expected_version=6,backup=backup)
        with CollaborationStore(old) as store:
            service = self.policy_service(store)
            history = service.list_requests(self.token,self.run["id"])["items"]
            self.assertEqual([(r["id"],r["status"],r["target_id"]) for r in history],[(first["id"],"approved",self.run["id"]),(pending["id"],"pending",self.run["id"])])
            self.assertEqual(self.decide(pending,service=service)["status"],"approved")
            runs = RunRepository(store,self.scope)
            run = runs.get(self.run["id"])
            created = service.create_request(self.token,self.run["id"],str(uuid4()),{"kind":"capability","old":{"execute":False},"new":{"execute":True},"reason":"reviewed","policy_version":run["policy_version"],"expected_revision":run["revision"]})
            self.assertEqual(created["sequence"],701)
            with store.transaction(write=False) as db:
                self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0],1)
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
                self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_requests WHERE action='request_budget_extension'").fetchone()[0],2)
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute("SELECT MAX(version) FROM collab_schema_migrations").fetchone()[0],6)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_approval_requests").fetchone()[0],2)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='collab_responsibility_versions'").fetchone())

    def test_rebuild_failure_after_table_drop_restores_exact_old_data_and_schema(self):
        self.request("budget_extension"); old = self.copy_v6(); backup = old.with_name("failure-backup.sqlite")
        original = storage.MIGRATIONS[6].read_bytes()
        self.assertEqual(original.count(b'DROP TABLE collab_policies;'),1)
        class BadMigration:
            def read_bytes(self): return original.replace(b'DROP TABLE collab_policies;',b'DROP TABLE collab_policies;\nSELECT * FROM fixture_injected_missing_table;',1)
        with patch.object(storage,"MIGRATIONS",storage.MIGRATIONS[:6]+(BadMigration(),)+storage.MIGRATIONS[7:]):
            with self.assertRaisesRegex(sqlite3.OperationalError,"fixture_injected_missing_table"):
                migrate_reviewed(old,expected_version=6,backup=backup)
        for path in [old,backup]:
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT MAX(version) FROM collab_schema_migrations").fetchone()[0],6)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_approval_requests").fetchone()[0],1)
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='collab_new_policies'").fetchone())

    def test_rebuild_rejects_foreign_key_corruption_before_commit(self):
        self.request("budget_extension"); old = self.copy_v6(); backup = old.with_name("integrity-backup.sqlite")
        original = storage.MIGRATIONS[6].read_bytes()
        class BadMigration:
            def read_bytes(self): return original+b"\nINSERT INTO collab_responsibility_versions VALUES ('bad','bad','bad','bad','bad',1,'[]');\n"
        with patch.object(storage,"MIGRATIONS",storage.MIGRATIONS[:6]+(BadMigration(),)+storage.MIGRATIONS[7:]):
            self.error("migration_integrity_failed",lambda:migrate_reviewed(old,expected_version=6,backup=backup))
        with closing(sqlite3.connect(old)) as db:
            self.assertEqual(db.execute("SELECT MAX(version) FROM collab_schema_migrations").fetchone()[0],6)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='collab_responsibility_versions'").fetchone())
