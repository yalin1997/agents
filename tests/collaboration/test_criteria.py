"""Real SQLite objective versions; no model, execution or gate pass is claimed."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from pi_deepagent.collaboration import criteria, storage
from pi_deepagent.collaboration.contracts import CollaborationError
from pi_deepagent.collaboration.policies import PolicyService
from pi_deepagent.collaboration.runs import RunRepository
from pi_deepagent.collaboration.storage import CollaborationStore, migrate_reviewed
from pi_deepagent.collaboration.work import WorkRepository
from pi_deepagent.web.auth import AuthError
from tests.collaboration.test_runs import RunFixture


def expected_hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")).hexdigest()


class CriteriaTests(RunFixture):
    def setUp(self):
        super().setUp(); self.activate()
        lead = next(m for m in self.run["members"] if m["spec"]["orchestrator"])
        self.lead = self.runs.bind_member(self.run["id"],lead["id"])
        self.work = WorkRepository(self.runs,self.auth.redactor)
        self.context = self.work.issue_initial_context(self.lead)
        self.item = self.work.create(self.context,str(uuid4()),{"goal":"Original work","criteria":["Original condition","Preserve history"]})
        self.policies = PolicyService(self.store,self.auth,self.workspace,clock_ms=lambda:self.now,ttl_seconds=2)

    def rows(self,sql,params=()):
        with self.store.transaction(write=False) as db: return [dict(r) for r in db.execute(sql,params)]

    def current(self,work=None): return self.policies.list_criteria(self.token,self.run["id"],work_id=work)["items"][-1]

    def payload(self,work=None,new=None):
        current = self.current(work); run = self.runs.get(self.run["id"])
        value = {"kind":"criteria_change","old":{key:current[key] for key in ("version","hash")},
                 "new":{"goal":"核准的新目標","criteria":["New explicit condition"]} if new is None else new,
                 "policy_version":run["policy_version"],"expected_revision":run["revision"],"reason":"Admin reviewed exact old and new requirements"}
        if work is not None: value["work_id"] = work
        return value

    def request(self,work=None,new=None,**changes):
        return self.policies.create_request(self.token,self.run["id"],str(uuid4()),self.payload(work,new)|changes)

    def decide(self,request,decision="approve",identifier=None,service=None):
        return (service or self.policies).decide(self.token,self.run["id"],identifier or str(uuid4()),
            {"approval_id":request["id"],"decision":decision,"expected_revision":request["revision"],"reason":"Reviewed explicit criteria replacement"})

    def error(self,code,call):
        with self.assertRaises(CollaborationError) as caught: call()
        self.assertEqual(caught.exception.code,code)
        return caught.exception

    def assign(self,item=None):
        item = item or self.work.get(self.run["id"],self.item["id"])
        actor = self.runs.bind_member(self.run["id"],self.lead.member_id)
        return self.work.prepare_initial_attempt(actor,item["id"],actor.member_id,str(uuid4()),expected_revision=item["revision"])

    def test_initial_documents_and_canonical_unicode_hashes(self):
        team = self.current(); work = self.current(self.item["id"])
        self.assertEqual((team["goal"],team["criteria"],team["version"],team["source"]),(self.config["goal"],self.config["criteria"],1,"initial"))
        original = {"goal":"Original work","criteria":["Original condition","Preserve history"]}
        self.assertEqual(work["hash"],expected_hash(original))
        self.assertEqual(self.work.get(self.run["id"],self.item["id"])["criteria_hash"],work["hash"])
        self.assertEqual(self.runs.get(self.run["id"])["objective"]["hash"],expected_hash({"goal":self.config["goal"],"criteria":self.config["criteria"]}))

    def test_team_change_appends_history_without_changing_original_config_snapshot_or_budget(self):
        before = self.runs.get(self.run["id"]); request = self.request(); result = self.decide(request)
        after = self.runs.get(self.run["id"])
        self.assertEqual((result["criteria_version"],result["policy_version"]),(2,2))
        self.assertEqual(after["config"],before["config"])
        for key in ("members","attempt","budgets","policy"): self.assertEqual(after[key],before[key])
        self.assertEqual(after["objective"]["goal"],"核准的新目標")
        self.assertEqual(after["objective"]["hash"],expected_hash(request["new"]))
        self.assertEqual(request["old"]["goal"],self.config["goal"])
        self.assertEqual([r["source"] for r in self.policies.list_criteria(self.token,self.run["id"])["items"]],["initial","approval"])
        self.assertEqual(self.current(self.item["id"])["version"],1)

    def test_work_change_updates_view_and_revision_keeps_raw_initial_values_and_roots(self):
        roots = self.rows("SELECT * FROM collab_repair_roots"); request = self.request(self.item["id"]); self.decide(request)
        current = self.work.get(self.run["id"],self.item["id"])
        self.assertEqual((current["goal"],current["criteria"],current["revision"],current["criteria_version"]),(request["new"]["goal"],request["new"]["criteria"],1,2))
        raw = self.rows("SELECT goal,criteria_json,required FROM collab_work_items WHERE id=?",(self.item["id"],))[0]
        self.assertEqual(raw,{"goal":"Original work","criteria_json":'["Original condition","Preserve history"]',"required":1})
        self.assertEqual(self.rows("SELECT * FROM collab_repair_roots"),roots)
        self.assertEqual(self.current()["version"],1)

    def test_admin_can_explicitly_replace_conditions_but_old_conditions_remain(self):
        new = {"goal":"Original work","criteria":["Replacement approved by admin"]}
        request = self.request(self.item["id"],new); self.decide(request)
        history = self.policies.list_criteria(self.token,self.run["id"],work_id=self.item["id"])["items"]
        self.assertEqual(history[0]["criteria"],["Original condition","Preserve history"])
        self.assertEqual(history[1]["criteria"],["Replacement approved by admin"])
        self.assertEqual(history[1]["approval_id"],request["id"])
        self.assertEqual(self.rows("SELECT decision FROM collab_approval_decisions"),[{"decision":"approve"}])

    def test_old_hash_version_or_text_cannot_be_forged_or_rebased(self):
        for old,code in [({"version":True,"hash":"a"*64},"invalid_integer"),({"version":1,"hash":"A"*64},"invalid_criteria_hash"),
                         ({"version":1,"hash":"a"*64},"approval_baseline_changed"),
                         ({"version":2,"hash":self.current()["hash"]},"approval_baseline_changed"),
                         ({"version":1,"hash":self.current()["hash"],"goal":"forged","criteria":["x"]},"invalid_criteria_hash")]:
            with self.subTest(code=code): self.error(code,lambda:self.request(old=old))
        self.assertEqual(self.rows("SELECT * FROM collab_approval_requests"),[])

    def test_new_document_requires_complete_nonempty_bounded_text_and_no_authority_fields(self):
        for new,code in [({},"invalid_fields"),({"goal":"x","criteria":[]},"invalid_criteria"),({"goal":"x","criteria":"x"},"invalid_criteria"),
                         ({"goal":"x","criteria":[True]},"invalid_text"),({"goal":"x"},"invalid_fields"),
                         ({"goal":"x","criteria":["x"],"required":False},"invalid_fields"),
                         ({"goal":"x"*131072,"criteria":["x"]},"criteria_too_large")]:
            with self.subTest(code=code): self.error(code,lambda:self.request(new=new))
        self.error("invalid_fields",lambda:self.request(member_id=self.lead.member_id))
        self.error("criteria_change_required",lambda:self.request(new={"goal":self.config["goal"],"criteria":self.config["criteria"]}))

    def test_attempt_snapshot_matches_initial_versions_and_later_team_change_invalidates_identity(self):
        attempt = self.assign(); original = self.rows("SELECT * FROM collab_work_attempts")
        refs = attempt["inputs"]["criteria_refs"]
        with self.store.transaction(write=False) as db: self.assertTrue(criteria.matches(db,self.run["id"],self.item["id"],refs))
        self.decide(self.request())
        with self.store.transaction(write=False) as db: self.assertFalse(criteria.matches(db,self.run["id"],self.item["id"],refs))
        self.assertEqual(self.rows("SELECT * FROM collab_work_attempts"),original)
        self.assertEqual(self.work.get(self.run["id"],self.item["id"])["status"],"ready")

    def test_new_attempt_uses_current_work_document_and_both_exact_references(self):
        self.decide(self.request(self.item["id"]))
        self.error("stale_revision",lambda:self.assign(self.item))
        attempt = self.assign()
        self.assertEqual((attempt["inputs"]["goal"],attempt["inputs"]["criteria"]),("核准的新目標",["New explicit condition"]))
        self.assertEqual(attempt["inputs"]["criteria_refs"]["work_criteria"],{"version":2,"hash":expected_hash({"goal":"核准的新目標","criteria":["New explicit condition"]})})
        self.assertEqual(attempt["inputs"]["criteria_refs"]["team_criteria"]["version"],1)

    def test_invalid_snapshot_shapes_boolean_versions_and_unknown_keys_fail_identity_check(self):
        with self.store.transaction(write=False) as db:
            valid = criteria.snapshot(db,self.run["id"],self.item["id"])
            for value in [None,{},valid|{"extra":True},valid|{"team_criteria":{"version":True,"hash":valid["team_criteria"]["hash"]}},
                          valid|{"work_criteria":{"version":1,"hash":"a"*64}}]:
                with self.subTest(value=value): self.assertFalse(criteria.matches(db,self.run["id"],self.item["id"],value))

    def test_replays_keep_original_approval_result_after_a_later_version(self):
        value = self.payload(self.item["id"]); request_id = str(uuid4())
        request = self.policies.create_request(self.token,self.run["id"],request_id,value)
        decision_id = str(uuid4()); first = self.decide(request,identifier=decision_id)
        self.decide(self.request(self.item["id"],{"goal":"Third goal","criteria":["Third condition"]}))
        self.assertEqual(self.policies.create_request(self.token,self.run["id"],request_id,value),request)
        self.assertEqual(self.decide(request,identifier=decision_id),first)
        self.assertEqual(self.current(self.item["id"])["version"],3)
        self.assertEqual(len(self.rows("SELECT * FROM collab_events WHERE kind='criteria_changed'")),2)
        self.error("request_conflict",lambda:self.policies.create_request(self.token,self.run["id"],request_id,value|{"reason":"different"}))

    def test_pending_second_change_is_superseded_after_first_approval(self):
        first = self.request(); second = self.request(self.item["id"])
        self.decide(first); self.error("approval_stale",lambda:self.decide(second))
        self.assertEqual(self.current(self.item["id"])["version"],1)
        self.assertEqual(self.rows("SELECT status FROM collab_approval_requests WHERE id=?",(second["id"],)),[{"status":"superseded"}])

    def test_parallel_approve_deny_has_one_winner_and_one_objective_version(self):
        request = self.request(self.item["id"]); barrier = Barrier(2)
        def race(decision):
            with CollaborationStore(self.path) as store:
                service = PolicyService(store,self.auth,self.workspace,clock_ms=lambda:self.now)
                barrier.wait(timeout=5)
                try: return self.decide(request,decision,service=service)["status"]
                except CollaborationError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(race,["approve","deny"]))
        self.assertEqual(results.count("approval_closed"),1)
        winner = next(x for x in results if x!="approval_closed")
        self.assertEqual(self.current(self.item["id"])["version"],2 if winner=="approved" else 1)
        self.assertEqual(len(self.rows("SELECT * FROM collab_approval_decisions")),1)

    def test_deny_expiry_and_stop_do_not_change_objective(self):
        first = self.request(); self.decide(first,"deny")
        second = self.request(); self.now += 2000; self.error("approval_expired",lambda:self.decide(second))
        third = self.request(); run = self.runs.get(self.run["id"])
        self.runs.stop(run["id"],str(uuid4()),expected_revision=run["revision"])
        self.error("approval_closed",lambda:self.decide(third))
        self.assertEqual(self.current()["version"],1)
        self.assertEqual(self.rows("SELECT * FROM collab_events WHERE kind='criteria_changed'"),[])

    def test_completed_work_is_not_reopened_and_pending_request_becomes_stale(self):
        request = self.request(self.item["id"])
        # Trusted persistence fixture; no real gate success is being asserted.
        with self.store.transaction() as db: db.execute("UPDATE collab_work_items SET status='completed' WHERE id=?",(self.item["id"],))
        self.error("work_closed",lambda:self.request(self.item["id"]))
        self.error("approval_stale",lambda:self.decide(request))
        self.assertEqual(self.current(self.item["id"])["version"],1)
        self.assertEqual(self.work.get(self.run["id"],self.item["id"])["status"],"completed")

    def test_history_auth_matrix_and_resource_scope(self):
        calls = [lambda token:self.policies.list_criteria(token,self.run["id"]),
                 lambda token:self.policies.list_criteria(token,self.run["id"],work_id=self.item["id"])]
        for call in calls:
            for token in [None,"invalid"]:
                with self.assertRaises(AuthError) as caught: call(token)
                self.assertEqual(caught.exception.status,401)
        key = self.auth._digest(self.token); original = self.auth.sessions[key]
        self.auth.sessions[key] = replace(original,role="reader")
        self.error("forbidden",lambda:calls[0](self.token))
        self.auth.sessions[key] = replace(original,expires_at=0)
        with self.assertRaises(AuthError): calls[1](self.token)
        self.auth.sessions[key] = original
        self.assertEqual(calls[0](self.token)["items"][0]["version"],1)
        denied = PolicyService(self.store,self.auth,self.workspace,allowed_actions=frozenset())
        self.error("forbidden",lambda:denied.list_criteria(self.token,self.run["id"]))
        self.auth.logout(self.token)
        with self.assertRaises(AuthError): calls[0](self.token)

    def test_foreign_work_and_workspace_cannot_read_or_amend(self):
        self.error("work_mismatch",lambda:self.policies.list_criteria(self.token,self.run["id"],work_id=str(uuid4())))
        value = self.payload(); value["work_id"] = str(uuid4())
        self.error("work_mismatch",lambda:self.policies.create_request(self.token,self.run["id"],str(uuid4()),value))
        directory = Path(self.temporary.name)/"foreign"; directory.mkdir()
        foreign = PolicyService(self.store,self.auth,directory)
        self.error("forbidden",lambda:foreign.list_criteria(self.token,self.run["id"]))
        self.error("forbidden",lambda:foreign.create_request(self.token,self.run["id"],str(uuid4()),self.payload()))

    def test_pagination_returns_all_immutable_versions(self):
        self.decide(self.request()); self.decide(self.request(new={"goal":"third","criteria":["third"]}))
        after = 0; versions = []
        for _ in range(3):
            page = self.policies.list_criteria(self.token,self.run["id"],after_version=after,limit=1)
            self.assertEqual(len(page["items"]),1); versions.append(page["items"][0]["version"]); after = page["next_cursor"]
        self.assertEqual(versions,[1,2,3]); self.assertFalse(page["has_more"])
        self.assertEqual(self.policies.list_criteria(self.token,self.run["id"],after_version=3)["items"],[])
        self.error("invalid_integer",lambda:self.policies.list_criteria(self.token,self.run["id"],limit=0))

    def test_work_and_run_cas_faults_roll_back_criteria_policy_ledger_event(self):
        for table,column in [("collab_work_items","target_id"),("collab_runs","run_id")]:
            request = self.request(self.item["id"]); before = self.runs.get(self.run["id"])
            with self.store.transaction() as db:
                db.execute("CREATE TRIGGER fixture_criteria_race AFTER INSERT ON collab_criteria_versions WHEN NEW.version>1 BEGIN "
                           "UPDATE "+table+" SET revision=revision+1 WHERE id=NEW."+column+"; END")
            self.error("stale_revision",lambda:self.decide(request))
            self.assertEqual(self.runs.get(self.run["id"]),before)
            self.assertEqual(self.current(self.item["id"])["version"],1)
            self.assertEqual(self.work.get(self.run["id"],self.item["id"])["revision"],0)
            self.assertEqual(self.rows("SELECT * FROM collab_requests WHERE action='decide_criteria_change'"),[])
            self.assertEqual(self.rows("SELECT * FROM collab_events WHERE kind='criteria_changed'"),[])
            with self.store.transaction() as db: db.execute("DROP TRIGGER fixture_criteria_race")

    def test_sql_objective_history_cannot_be_rewritten_deleted_or_skip_a_version(self):
        for query in ["UPDATE collab_criteria_versions SET payload='{}'","DELETE FROM collab_criteria_versions"]:
            with self.subTest(query=query),self.assertRaisesRegex(sqlite3.IntegrityError,"criteria_version_immutable"):
                with self.store.transaction() as db: db.execute(query)
        with self.assertRaisesRegex(sqlite3.IntegrityError,"criteria_version_required"):
            with self.store.transaction() as db:
                db.execute("INSERT INTO collab_criteria_versions SELECT run_id,target_type,target_id,5,payload,policy_version,'initial',NULL,created_at FROM collab_criteria_versions WHERE target_type='team_run'")

    def test_secrets_are_filtered_at_write_history_and_replay_without_copying_old_secret(self):
        self.auth.redactor.add("known-private")
        value = self.payload(new={"goal":"known-private","criteria":["known-private"]}); identifier = str(uuid4())
        request = self.policies.create_request(self.token,self.run["id"],identifier,value)
        self.assertEqual(request["new"],{"goal":"[REDACTED]","criteria":["[REDACTED]"]})
        self.decide(request)
        for table in ["collab_criteria_versions","collab_approval_requests","collab_events","collab_requests"]:
            self.assertNotIn("known-private",str(self.rows("SELECT * FROM "+table)))
        self.assertEqual(self.policies.create_request(self.token,self.run["id"],identifier,value),request)
        later = self.request(new={"goal":"later-private","criteria":["x"]})
        self.auth.redactor.add("later-private")
        self.error("criteria_redaction_changed",lambda:self.decide(later))
        self.assertEqual(self.current()["version"],2)
        self.assertEqual(self.rows("SELECT status FROM collab_approval_requests WHERE id=?",(later["id"],)),[{"status":"superseded"}])
        self.assertNotIn("later-private",str(self.policies.list_requests(self.token,self.run["id"])))
        fresh = self.runs.bind_member(self.run["id"],self.lead.member_id)
        context = self.work.issue_initial_context(fresh)
        old_item = self.work.create(context,str(uuid4()),{"goal":"old-private","criteria":["original"]})
        self.auth.redactor.add("old-private")
        self.assertEqual(self.work.get(self.run["id"],old_item["id"])["goal"],"[REDACTED]")
        safe = self.request(old_item["id"],{"goal":"Safe replacement","criteria":["safe"]})
        self.assertEqual(safe["old"]["goal"],"[REDACTED]")
        self.assertNotIn("old-private",str(self.rows("SELECT * FROM collab_approval_requests WHERE id=?",(safe["id"],))))
        self.decide(safe)
        self.assertEqual(self.current(old_item["id"])["goal"],"Safe replacement")

    def test_large_existing_objective_can_be_replaced_by_reference_without_losing_history(self):
        large = {"goal":"Large old objective","criteria":["x"*24000+str(i) for i in range(6)]}
        item = self.work.create(self.context,str(uuid4()),large)
        request = self.request(item["id"],{"goal":"Revised small objective","criteria":["small"]}); self.decide(request)
        history = self.policies.list_criteria(self.token,self.run["id"],work_id=item["id"])["items"]
        self.assertEqual(history[0]["criteria"],large["criteria"])
        self.assertEqual(history[1]["version"],2)

    def copy_v7(self):
        old = Path(self.temporary.name)/"v7.sqlite"
        with closing(sqlite3.connect(old)): pass
        migrate_reviewed(old,expected_version=0,target_version=7)
        with closing(sqlite3.connect(old)) as dest:
            dest.execute("PRAGMA foreign_keys=ON"); dest.execute("BEGIN"); dest.execute("PRAGMA defer_foreign_keys=ON")
            trigger = dest.execute("SELECT sql FROM sqlite_master WHERE name='collab_initial_work_root'").fetchone()[0]
            dest.execute("DROP TRIGGER collab_initial_work_root")
            names = [r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'collab_%' AND name!='collab_schema_migrations'")]
            with self.store.transaction(write=False) as source:
                for name in names:
                    cols = [r[1] for r in dest.execute('PRAGMA table_info("'+name+'")')]; quoted = ','.join('"'+c+'"' for c in cols)
                    rows = source.execute('SELECT '+quoted+' FROM "'+name+'"').fetchall()
                    dest.executemany('INSERT INTO "'+name+'" ('+quoted+') VALUES ('+','.join('?' for _ in cols)+')',[tuple(r) for r in rows])
            dest.execute(trigger); dest.commit()
        return old

    def test_v7_backfill_keeps_original_config_and_legacy_attempt_bytes_with_backup(self):
        old = self.copy_v7(); backup = old.with_name("v7-backup.sqlite"); attempt = str(uuid4())
        legacy = '{"legacy":"exact-original-input"}'
        with closing(sqlite3.connect(old)) as db:
            db.execute("INSERT INTO collab_work_attempts(id,work_id,run_id,run_attempt_id,member_id,number,status,inputs_json,outcome_json) VALUES (?,?,?,?,?,1,'failed',?,?)",
                       (attempt,self.item["id"],self.run["id"],self.lead.attempt_id,self.lead.member_id,legacy,'{"certainty":"known"}'))
            db.commit()
        migrate_reviewed(old,expected_version=7,backup=backup)
        with CollaborationStore(old) as store:
            service = PolicyService(store,self.auth,self.workspace)
            work = service.list_criteria(self.token,self.run["id"],work_id=self.item["id"])["items"][0]
            self.assertEqual((work["version"],work["source"],work["goal"]),(1,"migration","Original work"))
            self.assertEqual(work["hash"],self.item["criteria_hash"])
            with store.transaction(write=False) as db:
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
                self.assertEqual(db.execute("SELECT inputs_json FROM collab_work_attempts WHERE id=?",(attempt,)).fetchone()[0],legacy)
                self.assertFalse(criteria.matches(db,self.run["id"],self.item["id"],json.loads(legacy)))
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute("SELECT MAX(version) FROM collab_schema_migrations").fetchone()[0],7)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='collab_criteria_versions'").fetchone())
            self.assertEqual(db.execute("SELECT inputs_json FROM collab_work_attempts WHERE id=?",(attempt,)).fetchone()[0],legacy)

    def test_v8_ddl_failure_rolls_back_backfill_and_preserves_v7_backup(self):
        old = self.copy_v7(); backup = old.with_name("v7-failure-backup.sqlite")
        original = storage.MIGRATIONS[7].read_bytes()
        class BadMigration:
            def read_bytes(self): return original+b'\nSELECT * FROM fixture_criteria_missing_table;\n'
        with patch.object(storage,"MIGRATIONS",storage.MIGRATIONS[:7]+(BadMigration(),)+storage.MIGRATIONS[8:]):
            with self.assertRaisesRegex(sqlite3.OperationalError,"fixture_criteria_missing_table"):
                migrate_reviewed(old,expected_version=7,backup=backup)
        for path in [old,backup]:
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT MAX(version) FROM collab_schema_migrations").fetchone()[0],7)
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='collab_criteria_versions'").fetchone())
