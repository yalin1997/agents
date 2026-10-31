"""Real SQLite approval CAS and immutable policy history; no execution claims."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
from threading import Barrier
from uuid import uuid4

from pi_deepagent.collaboration.contracts import CollaborationError
from pi_deepagent.collaboration.mailbox import MailboxRepository
from pi_deepagent.collaboration.policies import PolicyService
from pi_deepagent.collaboration.runs import RunRepository
from pi_deepagent.collaboration.storage import CollaborationStore, SCHEMA_VERSION, migrate_reviewed
from pi_deepagent.web.auth import AuthError
from tests.collaboration.test_runs import RunFixture


class PolicyTests(RunFixture):
    def setUp(self):
        super().setUp()
        self.actor = self.activate()
        self.policies = PolicyService(self.store, self.auth, self.workspace, clock_ms=lambda: self.now, ttl_seconds=2)

    def rows(self, query, parameters=()):
        with self.store.transaction(write=False) as db: return [dict(row) for row in db.execute(query, parameters)]

    def payload(self, new=None):
        run = self.runs.get(self.run["id"])
        new = {"model_calls":4} if new is None else new
        return {"kind":"budget_extension", "old":{key:run["policy"]["limits"][key] for key in new}, "new":new,
                "policy_version":run["policy_version"], "expected_revision":run["revision"], "reason":"Remaining required checks need more total budget"}

    def request(self, new=None):
        return self.policies.create_request(self.token,self.run["id"],str(uuid4()),self.payload(new))

    def decision(self, request, decision="approve"):
        return {"approval_id":request["id"],"decision":decision,"expected_revision":request["revision"],"reason":"Reviewed explicit total limits"}

    def decide(self, request, decision="approve"):
        return self.policies.decide(self.token,self.run["id"],str(uuid4()),self.decision(request,decision))

    def test_approval_adds_policy_version_and_preserves_usage_snapshots_and_attempt(self):
        reservation = self.budgets.reserve(self.actor,str(uuid4()),"model_calls")
        self.budgets.settle(self.actor,reservation["id"],str(uuid4()),certainty="known",started=True)
        before = self.runs.get(self.run["id"])
        request = self.request({"model_calls":4,"messages":5,"wall_seconds":20})
        self.assertEqual(self.runs.get(self.run["id"])["policy"],before["policy"])
        self.assertEqual(request["status"],"pending")
        approved = self.decide(request)
        current = self.runs.get(self.run["id"])
        self.assertEqual((approved["status"],current["policy_version"],current["status"]),("approved",2,"running"))
        self.assertEqual(current["budgets"],before["budgets"])
        self.assertEqual(current["attempt"],before["attempt"])
        self.assertEqual((current["members"],current["config"]),(before["members"],before["config"]))
        for capability in ("execute","network","dependency_install","publish"):
            self.assertEqual(current["policy"][capability],before["policy"][capability])
        history = self.policies.list_policies(self.token,current["id"])["items"]
        self.assertEqual([row["version"] for row in history],[1,2])
        self.assertEqual(history[0]["policy"],before["policy"])
        self.assertEqual((history[1]["source"],history[1]["approval_id"]),("budget_extension",request["id"]))
        detail = self.policies.list_requests(self.token,current["id"])["items"][0]
        self.assertEqual((detail["decision"]["actor"],detail["decision"]["policy_version"]),("admin",2))

    def test_exact_replays_apply_once_and_different_content_conflicts(self):
        value,request_id = self.payload(),str(uuid4())
        request = self.policies.create_request(self.token,self.run["id"],request_id,value)
        self.assertEqual(self.policies.create_request(self.token,self.run["id"],request_id,value),request)
        with self.assertRaises(CollaborationError) as raised:
            self.policies.create_request(self.token,self.run["id"],request_id,{**value,"new":{"model_calls":5}})
        self.assertEqual(raised.exception.code,"request_conflict")
        decision,decision_id = self.decision(request),str(uuid4())
        result = self.policies.decide(self.token,self.run["id"],decision_id,decision)
        self.assertEqual(self.policies.decide(self.token,self.run["id"],decision_id,decision),result)
        with self.assertRaises(CollaborationError) as raised:
            self.policies.decide(self.token,self.run["id"],decision_id,{**decision,"decision":"deny"})
        self.assertEqual(raised.exception.code,"request_conflict")
        with self.assertRaises(CollaborationError) as raised: self.decide(request)
        self.assertEqual(raised.exception.code,"approval_closed")
        self.assertEqual(len(self.rows("SELECT * FROM collab_policies")),2)
        self.assertEqual(len(self.rows("SELECT * FROM collab_approval_decisions")),1)

    def test_old_policy_cannot_dispatch_but_known_operation_and_turn_can_settle(self):
        peer = self.runs.bind_member(self.run["id"],self.run["members"][1]["id"])
        mailbox = MailboxRepository(self.runs,self.auth.redactor)
        mailbox.send(peer,str(uuid4()),{"recipient":self.actor.member_id,"kind":"status","body":"Existing turn input"})
        batch = mailbox.claim(self.actor,str(uuid4()))
        reservation = self.budgets.reserve(self.actor,str(uuid4()),"model_calls")
        self.decide(self.request({"model_calls":4,"messages":6}))
        for operation in (lambda:self.budgets.reserve(self.actor,str(uuid4()),"model_calls"), lambda:mailbox.claim(self.actor,str(uuid4()))):
            with self.assertRaises(CollaborationError) as raised: operation()
            self.assertEqual(raised.exception.code,"stale_policy")
        self.assertEqual(self.budgets.settle(self.actor,reservation["id"],str(uuid4()),certainty="known",started=True)["status"],"consumed")
        turn = batch["turn"]
        self.assertEqual(mailbox.finish_turn(self.actor,turn["id"],str(uuid4()),fence_id=turn["fence_id"],expected_revision=0,outcome="completed")["status"],"completed")
        fresh = self.runs.bind_member(self.run["id"],self.actor.member_id)
        self.assertEqual(fresh.policy_version,2)
        self.assertEqual(self.budgets.reserve(fresh,str(uuid4()),"model_calls")["status"],"reserved")

    def test_unknown_reservation_cannot_be_reset_by_an_approved_extension(self):
        reservation = self.budgets.reserve(self.actor,str(uuid4()),"model_calls")
        self.budgets.settle(self.actor,reservation["id"],str(uuid4()),certainty="unknown",started=True)
        self.decide(self.request())
        fresh = self.runs.bind_member(self.run["id"],self.actor.member_id)
        with self.assertRaises(CollaborationError) as raised: self.budgets.reserve(fresh,str(uuid4()),"model_calls")
        self.assertEqual(raised.exception.code,"actor_operation_unknown")
        self.assertEqual(self.runs.get(self.run["id"])["budgets"]["model_calls"],{"used":0,"reserved":1})
        run = self.runs.get(self.run["id"])
        self.runs.stop(run["id"],str(uuid4()),expected_revision=run["revision"])
        with self.assertRaises(CollaborationError) as raised:
            self.runs.finish_stopping(run["id"],str(uuid4()),attempt_id=self.actor.attempt_id)
        self.assertEqual(raised.exception.code,"resources_not_drained")

    def test_failed_run_extension_does_not_resume_or_edit_old_attempt(self):
        for repeat in range(2):
            reservation = self.budgets.reserve(self.actor,str(uuid4()),"model_calls")
            self.budgets.settle(self.actor,reservation["id"],str(uuid4()),certainty="known",started=True)
        with self.assertRaises(CollaborationError): self.budgets.reserve(self.actor,str(uuid4()),"model_calls")
        failed = self.runs.finish_stopping(self.run["id"],str(uuid4()),attempt_id=self.actor.attempt_id)
        old_attempt = dict(failed["attempt"])
        self.decide(self.request())
        current = self.runs.get(self.run["id"])
        self.assertEqual((current["status"],current["attempt"],current["budgets"]["model_calls"]),("failed",old_attempt,{"used":2,"reserved":0}))
        queued = self.runs.continue_run(current["id"],str(uuid4()),expected_revision=current["revision"])
        self.assertEqual((queued["status"],queued["attempt"]["number"]),("queued",2))
        self.assertEqual(self.rows("SELECT * FROM collab_run_attempts WHERE id=?",(old_attempt["id"],))[0],old_attempt)

    def test_expired_wall_budget_closes_approval_instead_of_reviving_attempt(self):
        request = self.request({"wall_seconds":20}); self.now += 10_000
        with self.assertRaises(CollaborationError) as raised: self.decide(request)
        self.assertEqual(raised.exception.code,"approval_closed")
        run = self.runs.get(self.run["id"])
        self.assertEqual((run["status"],run["policy_version"],run["wall_used_ms"]),("stopping",1,10_000))
        self.assertEqual(self.rows("SELECT status FROM collab_approval_requests")[0]["status"],"cancelled")
        self.runs.finish_stopping(run["id"],str(uuid4()),attempt_id=self.actor.attempt_id)
        self.decide(self.request({"wall_seconds":20}))
        self.assertEqual(self.runs.get(run["id"])["status"],"failed")

    def test_another_approval_makes_prior_policy_request_stale_without_rebasing(self):
        first = self.request({"model_calls":4}); second = self.request({"messages":5})
        self.decide(first)
        with self.assertRaises(CollaborationError) as raised: self.decide(second)
        self.assertEqual(raised.exception.code,"approval_stale")
        run = self.runs.get(self.run["id"])
        self.assertEqual((run["policy_version"],run["policy"]["limits"]["messages"]),(2,3))
        self.assertEqual(self.rows("SELECT status FROM collab_approval_requests WHERE id=?",(second["id"],))[0]["status"],"superseded")
        self.assertEqual(len(self.rows("SELECT * FROM collab_approval_decisions")),1)

    def test_parallel_approve_and_deny_have_only_one_persisted_winner(self):
        request,barrier = self.request(),Barrier(2)
        def race(decision):
            with CollaborationStore(self.path) as store:
                service = PolicyService(store,self.auth,self.workspace,clock_ms=lambda:self.now)
                barrier.wait(timeout=5)
                try: return service.decide(self.token,self.run["id"],str(uuid4()),self.decision(request,decision))["status"]
                except CollaborationError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(race,("approve","deny")))
        self.assertEqual(results.count("approval_closed"),1)
        winner = next(value for value in results if value!="approval_closed")
        self.assertIn(winner,("approved","denied"))
        self.assertEqual(len(self.rows("SELECT * FROM collab_approval_decisions")),1)
        self.assertEqual(self.runs.get(self.run["id"])["policy_version"],2 if winner=="approved" else 1)

    def test_parallel_same_request_returns_original_and_stale_run_revision_refuses_new_request(self):
        value,request_id,barrier = self.payload(),str(uuid4()),Barrier(2)
        def create(index):
            with CollaborationStore(self.path) as store:
                service = PolicyService(store,self.auth,self.workspace,clock_ms=lambda:self.now)
                barrier.wait(timeout=5)
                return service.create_request(self.token,self.run["id"],request_id,value)
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(create,range(2)))
        self.assertEqual(results[0],results[1])
        with self.assertRaises(CollaborationError) as raised:
            self.policies.create_request(self.token,self.run["id"],str(uuid4()),value)
        self.assertEqual(raised.exception.code,"stale_revision")
        self.assertEqual(len(self.rows("SELECT * FROM collab_approval_requests")),1)

    def test_expiration_and_denial_do_not_change_policy_or_autorequest(self):
        request = self.request(); self.now += 2000
        request_id = str(uuid4()); value = self.decision(request)
        for repeat in range(2):
            with self.assertRaises(CollaborationError) as raised:
                self.policies.decide(self.token,self.run["id"],request_id,value)
            self.assertEqual(raised.exception.code,"approval_expired")
        denied = self.request({"messages":5}); self.decide(denied,"deny")
        self.assertEqual([row["status"] for row in self.policies.list_requests(self.token,self.run["id"])["items"]],["expired","denied"])
        self.assertEqual(self.runs.get(self.run["id"])["policy_version"],1)
        self.assertEqual(len(self.rows("SELECT * FROM collab_approval_requests")),2)
        self.assertIsNone(self.rows("SELECT applied_policy_version FROM collab_approval_decisions")[0]["applied_policy_version"])

    def test_stop_and_interruption_cancel_pending_approval(self):
        request = self.request(); run = self.runs.get(self.run["id"])
        self.runs.stop(run["id"],str(uuid4()),expected_revision=run["revision"])
        with self.assertRaises(CollaborationError) as raised: self.decide(request)
        self.assertEqual(raised.exception.code,"approval_closed")
        self.assertEqual(self.rows("SELECT status FROM collab_approval_requests")[0]["status"],"cancelled")
        stopped = self.runs.finish_stopping(run["id"],str(uuid4()),attempt_id=self.actor.attempt_id)
        self.run = self.runs.continue_run(run["id"],str(uuid4()),expected_revision=stopped["revision"])
        self.actor = self.activate(); other = self.request()
        self.runs.mark_interrupted(run["id"],str(uuid4()),attempt_id=self.actor.attempt_id)
        with self.assertRaises(CollaborationError) as raised: self.decide(other)
        self.assertEqual(raised.exception.code,"approval_closed")
        self.assertEqual(self.runs.get(run["id"])["status"],"interrupted")

    def test_all_methods_require_valid_admin_operation_and_session_before_replay(self):
        request = self.request(); value = self.decision(request); request_id = str(uuid4())
        def operations(token):
            return (lambda:self.policies.list_requests(token,self.run["id"]), lambda:self.policies.list_policies(token,self.run["id"]),
                    lambda:self.policies.create_request(token,self.run["id"],str(uuid4()),self.payload()),
                    lambda:self.policies.decide(token,self.run["id"],request_id,value))
        for token in (None,"","invalid"):
            for operation in operations(token):
                with self.assertRaises(AuthError) as raised: operation()
                self.assertEqual(raised.exception.status,401)
        key = self.auth._digest(self.token); original = self.auth.sessions[key]
        self.auth.sessions[key] = replace(original,role="reader")
        for operation in operations(self.token):
            with self.assertRaises(CollaborationError) as raised: operation()
            self.assertEqual(raised.exception.status,403)
        self.auth.sessions[key] = original
        readonly = PolicyService(self.store,self.auth,self.workspace,allowed_actions={"read"})
        with self.assertRaises(CollaborationError) as raised: readonly.decide(self.token,self.run["id"],str(uuid4()),value)
        self.assertEqual(raised.exception.status,403)
        self.policies.decide(self.token,self.run["id"],request_id,value)
        self.auth.logout(self.token)
        for operation in operations(self.token):
            with self.assertRaises(AuthError) as raised: operation()
            self.assertEqual(raised.exception.status,401)
        token,_ = self.auth.login("admin","temporary-run-fixture-admin-123","test")
        self.auth.sessions[self.auth._digest(token)] = replace(original,expires_at=0)
        for operation in operations(token):
            with self.assertRaises(AuthError): operation()

    def test_foreign_run_and_workspace_cannot_replay_an_approval(self):
        request = self.request(); decision_id = str(uuid4()); value = self.decision(request)
        self.policies.decide(self.token,self.run["id"],decision_id,value)
        self.finish()
        other_team = self.service.create_team(self.token,str(uuid4()),self.config)
        other = self.runs.prepare(other_team["id"],str(uuid4()),expected_revision=0)
        with self.assertRaises(CollaborationError) as raised: self.policies.decide(self.token,other["id"],decision_id,value)
        self.assertEqual((raised.exception.status,raised.exception.code),(403,"approval_mismatch"))
        directory = Path(self.temporary.name)/"foreign"; directory.mkdir()
        outsider = PolicyService(self.store,self.auth,directory)
        with self.assertRaises(CollaborationError) as raised: outsider.decide(self.token,self.run["id"],decision_id,value)
        self.assertEqual(raised.exception.status,403)

    def test_stale_policy_or_request_revision_and_invalid_decisions_cannot_apply(self):
        value = self.payload()
        with self.assertRaises(CollaborationError) as raised:
            self.policies.create_request(self.token,self.run["id"],str(uuid4()),{**value,"policy_version":99})
        self.assertEqual(raised.exception.code,"stale_policy")
        request = self.request()
        for change,code in (({"expected_revision":99},"stale_revision"),({"decision":"enable_all"},"invalid_approval_decision"),
                            ({"actor":"admin"},"invalid_fields")):
            with self.subTest(change=change), self.assertRaises(CollaborationError) as raised:
                self.policies.decide(self.token,self.run["id"],str(uuid4()),{**self.decision(request),**change})
            self.assertEqual(raised.exception.code,code)
        self.assertEqual(self.rows("SELECT status FROM collab_approval_requests")[0]["status"],"pending")
        self.assertEqual(self.runs.get(self.run["id"])["policy_version"],1)

    def test_paged_history_keeps_redacted_reasons_and_exact_raw_content_identity(self):
        secrets = ("policy-secret-one","policy-secret-two")
        self.auth.redactor.add(*secrets)
        value,request_id = {**self.payload(),"reason":secrets[0]},str(uuid4())
        first = self.policies.create_request(self.token,self.run["id"],request_id,value)
        self.assertEqual(first["reason"],"[REDACTED]")
        with self.assertRaises(CollaborationError) as raised:
            self.policies.create_request(self.token,self.run["id"],request_id,{**value,"reason":secrets[1]})
        self.assertEqual(raised.exception.code,"request_conflict")
        second = self.request({"messages":5})
        page = self.policies.list_requests(self.token,self.run["id"],limit=1)
        self.assertTrue(page["has_more"])
        self.assertEqual([row["id"] for row in page["items"]],[first["id"]])
        self.assertEqual([row["id"] for row in self.policies.list_requests(self.token,self.run["id"],after_sequence=page["next_cursor"])["items"]],[second["id"]])
        self.policies.decide(self.token,self.run["id"],str(uuid4()),{**self.decision(first),"reason":secrets[1]})
        history = self.policies.list_policies(self.token,self.run["id"],limit=1)
        self.assertTrue(history["has_more"])
        new = self.policies.list_policies(self.token,self.run["id"],after_version=history["next_cursor"])["items"]
        self.assertEqual((new[0]["version"],new[0]["reason"]),(2,"[REDACTED]"))
        self.assertNotEqual(history["items"][0]["hash"],new[0]["hash"])
        for table in ("collab_policies","collab_approval_requests","collab_approval_decisions","collab_events","collab_requests"):
            contents = str(self.rows("SELECT * FROM "+table))
            for secret in secrets: self.assertNotIn(secret,contents)

    def test_invalid_targets_baselines_limits_and_kinds_are_not_policy_authority(self):
        value = self.payload()
        for changed,code in (({"new":{"model_calls":1}},"budget_increase_required"),({"new":{"model_calls":True}},"invalid_integer"),
                             ({"old":{"model_calls":1}},"budget_baseline_changed"),({"kind":"unknown"},"unsupported_approval_kind"),
                             ({"kind":"capability"},"unsupported_capability_target"),
                             ({"new":{"execute":True}},"unsupported_budget_target"),({"target_type":"host"},"invalid_fields"),
                             ({"old":{"repair_attempts":3},"new":{"repair_attempts":5}},"unsupported_budget_target"),
                             ({"work_id":str(uuid4())},"work_mismatch")):
            with self.subTest(changed=changed), self.assertRaises(CollaborationError) as raised:
                self.policies.create_request(self.token,self.run["id"],str(uuid4()),{**value,**changed})
            self.assertEqual(raised.exception.code,code)
        self.assertEqual(len(self.rows("SELECT * FROM collab_approval_requests")),0)
        self.assertEqual(self.runs.get(self.run["id"])["policy_version"],1)

    def test_sql_guards_prevent_policy_or_approval_history_rewrite(self):
        request = self.request(); self.decide(request)
        for query in ("UPDATE collab_policies SET payload='{}'", "UPDATE collab_runs SET policy_json='{}'", "UPDATE collab_runs SET policy_version=99",
                      "UPDATE collab_approval_requests SET old_json='{}'", "UPDATE collab_approval_requests SET status='pending',revision=revision+1",
                      "UPDATE collab_approval_decisions SET decision='deny'"):
            with self.subTest(query=query), self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as db: db.execute(query)

    def test_failed_decision_rolls_back_policy_request_event_and_ledger(self):
        request = self.request(); before = self.runs.get(self.run["id"])
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fixture_deny_decision BEFORE INSERT ON collab_approval_decisions "
                       "BEGIN SELECT RAISE(ABORT,'injected policy decision failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError,"injected policy decision failure"): self.decide(request)
        self.assertEqual(self.runs.get(self.run["id"]),before)
        self.assertEqual(self.rows("SELECT status FROM collab_approval_requests")[0]["status"],"pending")
        self.assertEqual(len(self.rows("SELECT * FROM collab_policies")),1)
        self.assertEqual(self.rows("SELECT * FROM collab_requests WHERE action='decide_budget_extension'"),[])
        self.assertEqual(self.rows("SELECT * FROM collab_events WHERE kind='approval_approved'"),[])

    def test_policy_publication_cas_rejects_revision_change_inside_transaction(self):
        request = self.request(); before = self.runs.get(self.run["id"])
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fixture_change_revision AFTER INSERT ON collab_policies WHEN NEW.version>1 "
                       "BEGIN UPDATE collab_runs SET revision=revision+1 WHERE id=NEW.run_id; END")
        with self.assertRaises(CollaborationError) as raised: self.decide(request)
        self.assertEqual(raised.exception.code,"stale_revision")
        self.assertEqual(self.runs.get(self.run["id"]),before)
        self.assertEqual(len(self.rows("SELECT * FROM collab_policies")),1)
        self.assertEqual(self.rows("SELECT status FROM collab_approval_requests")[0]["status"],"pending")

    def test_stopping_or_archived_run_cannot_create_policy_requests(self):
        run = self.runs.get(self.run["id"])
        self.runs.stop(run["id"],str(uuid4()),expected_revision=run["revision"])
        with self.assertRaises(CollaborationError) as raised: self.request()
        self.assertEqual(raised.exception.code,"policy_change_closed")
        self.runs.finish_stopping(run["id"],str(uuid4()),attempt_id=self.actor.attempt_id)
        with self.store.transaction() as db: db.execute("UPDATE collab_teams SET status='archived'")
        with self.assertRaises(CollaborationError) as raised: self.request()
        self.assertEqual(raised.exception.code,"team_archived")
        self.assertEqual(self.rows("SELECT * FROM collab_approval_requests"),[])

    def test_real_version_four_data_upgrades_with_policy_snapshot_and_backup_intact(self):
        old = Path(self.temporary.name)/"version-four.sqlite"
        with closing(sqlite3.connect(old)): pass
        migrate_reviewed(old,expected_version=0,target_version=4)
        with closing(sqlite3.connect(old)) as dest:
            dest.execute("PRAGMA foreign_keys=ON"); dest.execute("BEGIN"); dest.execute("PRAGMA defer_foreign_keys=ON")
            names = [row[0] for row in dest.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'collab_%' AND name!='collab_schema_migrations'")]
            with self.store.transaction(write=False) as source:
                for name in names:
                    columns = ','.join('"'+row[1]+'"' for row in dest.execute('PRAGMA table_info("'+name+'")'))
                    rows = source.execute('SELECT '+columns+' FROM "'+name+'"').fetchall()
                    placeholders = ','.join('?' for _ in dest.execute('PRAGMA table_info("'+name+'")'))
                    dest.executemany('INSERT INTO "'+name+'" ('+columns+') VALUES ('+placeholders+')',[tuple(row) for row in rows])
            dest.commit()
        backup = old.with_name("version-four-backup.sqlite")
        migrate_reviewed(old,expected_version=4,backup=backup)
        with CollaborationStore(old) as store:
            migrated = RunRepository(store,self.scope).get(self.run["id"])
            self.assertEqual(migrated,self.runs.get(self.run["id"]))
            history = PolicyService(store,self.auth,self.workspace).list_policies(self.token,self.run["id"])["items"]
            self.assertEqual((history[0]["version"],history[0]["policy"],history[0]["source"]),(1,migrated["policy"],"migration"))
            with store.transaction(write=False) as db:
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
                self.assertEqual(db.execute("SELECT MAX(version) FROM collab_schema_migrations").fetchone()[0],SCHEMA_VERSION)
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute("SELECT MAX(version) FROM collab_schema_migrations").fetchone()[0],4)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='collab_policies'").fetchone())
            self.assertEqual(db.execute("SELECT policy_json FROM collab_runs").fetchone()[0],self.rows("SELECT policy_json FROM collab_runs")[0]["policy_json"])
