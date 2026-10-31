"""Typed questions with real auth, SQLite, claim fencing and concurrent answers."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sqlite3
from threading import Barrier
from uuid import uuid4

from pi_deepagent.collaboration.contracts import CollaborationError
from pi_deepagent.collaboration.mailbox import MailboxRepository
from pi_deepagent.collaboration.questions import QuestionRepository, QuestionService
from pi_deepagent.collaboration.runs import RunRepository
from pi_deepagent.collaboration.storage import CollaborationStore
from pi_deepagent.web.auth import AuthError
from tests.collaboration.test_runs import RunFixture


class QuestionTests(RunFixture):
    def setUp(self):
        super().setUp()
        self.actor = self.activate()
        self.peer = self.runs.bind_member(self.run["id"], self.run["members"][1]["id"])
        self.questions = QuestionRepository(self.runs, self.auth.redactor, ttl_seconds=2)
        self.admin = QuestionService(self.store, self.auth, self.workspace, clock_ms=lambda: self.now)
        self.mailbox = MailboxRepository(self.runs, self.auth.redactor)
        self.work_id, self.work_attempt_id = self.seed_work(self.actor)

    def seed_work(self, binding):
        work_id, attempt_id = str(uuid4()), str(uuid4())
        with self.store.transaction() as db:
            db.execute("INSERT INTO collab_work_items(id,run_id,repair_root_id,goal,criteria_json,required,status) VALUES (?,?,?,?,?,1,'running')",
                       (work_id, binding.run_id, work_id, "Fixture work; no execution", '["required evidence"]'))
            db.execute("INSERT INTO collab_work_attempts(id,work_id,run_id,run_attempt_id,member_id,number,status,inputs_json) "
                       "VALUES (?,?,?,?,?,1,'running','{}')", (attempt_id, work_id, binding.run_id, binding.attempt_id, binding.member_id))
        return work_id, attempt_id

    def ask(self, value=None, request_id=None):
        return self.questions.ask(self.actor, self.work_attempt_id, request_id or str(uuid4()),
                                  {"prompt": "Which behavior is required?"} if value is None else value)

    def answer(self, question, *, outcome="answered", text="Use the documented behavior", request_id=None, token=None):
        return self.admin.answer(self.token if token is None else token, self.run["id"], request_id or str(uuid4()),
            {"question_id": question["id"], "answer": {"outcome": outcome, "text": text}, "expected_revision": question["revision"]})

    def finish_batch(self, batch, *, outcome="completed"):
        turn = batch["turn"]
        return self.mailbox.finish_turn(self.actor, turn["id"], str(uuid4()), fence_id=turn["fence_id"], expected_revision=0, outcome=outcome)

    def rows(self, query, parameters=()):
        with self.store.transaction(write=False) as db:
            return [dict(row) for row in db.execute(query, parameters)]

    def test_answer_routes_to_original_member_and_same_work_attempt_only(self):
        question = self.ask({"prompt": "Choose a mode", "options": ["strict", "lenient"]})
        before = self.rows("SELECT * FROM collab_work_attempts")
        response = self.answer(question)
        self.assertEqual((response["member_id"], response["work_attempt_id"], response["status"]),
                         (self.actor.member_id, self.work_attempt_id, "accepted"))
        self.assertEqual(self.rows("SELECT * FROM collab_work_attempts"), before)
        self.assertEqual(len(self.rows("SELECT * FROM collab_run_attempts")), 1)
        self.assertEqual(self.rows("SELECT status FROM collab_work_items")[0]["status"], "ready")
        self.assertEqual(self.mailbox.claim(self.peer, str(uuid4()))["answers"], [])
        self.assertEqual(self.mailbox.claim(self.actor, str(uuid4()))["answers"], [])
        batch = self.mailbox.claim(self.actor, str(uuid4()), work_attempt_id=self.work_attempt_id)
        self.assertEqual(len(batch["answers"]), 1)
        delivered = batch["answers"][0]
        self.assertEqual((delivered["question_id"], delivered["work_attempt_id"], delivered["sender"]),
                         (question["id"], self.work_attempt_id, {"type": "admin", "name": "admin"}))
        self.assertEqual(delivered["body"], "Use the documented behavior")
        self.assertEqual(self.finish_batch(batch)["pending_messages"], 0)
        self.assertEqual(self.rows("SELECT status FROM collab_answer_deliveries")[0]["status"], "processed")
        detail = self.admin.list_questions(self.token, self.run["id"])["items"][0]
        self.assertEqual(detail["answer"]["text"], delivered["body"])
        self.assertEqual(self.runs.get(self.run["id"])["budgets"]["messages"], {"used": 1, "reserved": 0})

    def test_exact_replays_do_not_recreate_question_answer_or_budget(self):
        request = str(uuid4()); question = self.ask(request_id=request)
        self.assertEqual(self.ask(request_id=request), question)
        with self.assertRaises(CollaborationError) as raised: self.ask(request_id=request, value={"prompt": "Changed"})
        self.assertEqual(raised.exception.code, "request_conflict")
        with self.assertRaises(CollaborationError) as raised: self.ask()
        self.assertEqual(raised.exception.code, "question_pending")
        answer_id = str(uuid4()); accepted = self.answer(question, request_id=answer_id)
        self.assertEqual(self.answer(question, request_id=answer_id), accepted)
        with self.assertRaises(CollaborationError) as raised: self.answer(question, request_id=answer_id, text="Changed")
        self.assertEqual(raised.exception.code, "request_conflict")
        with self.assertRaises(CollaborationError) as raised: self.answer(question)
        self.assertEqual(raised.exception.code, "question_closed")
        self.assertEqual(len(self.rows("SELECT * FROM collab_decisions")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM collab_decision_answers")), 1)
        self.assertEqual(self.rows("SELECT last_sequence FROM collab_mailbox_cursors")[0]["last_sequence"], 1)

    def test_authentication_permissions_and_revocation_precede_answer_replay(self):
        question = self.ask()
        payload = {"question_id": question["id"], "answer": {"outcome": "answered", "text": "OK"}, "expected_revision": 0}
        def operations(token):
            return (lambda: self.admin.list_questions(token, self.run["id"]),
                    lambda: self.admin.answer(token, self.run["id"], str(uuid4()), payload))
        for token in ("", None, "invalid"):
            for operation in operations(token):
                with self.assertRaises(AuthError) as raised: operation()
                self.assertEqual(raised.exception.status, 401)
        key = self.auth._digest(self.token); original = self.auth.sessions[key]
        self.auth.sessions[key] = replace(original, role="reader")
        for operation in operations(self.token):
            with self.assertRaises(CollaborationError) as raised: operation()
            self.assertEqual(raised.exception.status, 403)
        self.auth.sessions[key] = original
        limited = QuestionService(self.store, self.auth, self.workspace, allowed_actions={"read"})
        with self.assertRaises(CollaborationError) as raised: limited.answer(self.token, self.run["id"], str(uuid4()), payload)
        self.assertEqual(raised.exception.status, 403)
        request = str(uuid4()); self.answer(question, request_id=request)
        self.auth.logout(self.token)
        for operation in operations(self.token):
            with self.assertRaises(AuthError) as raised: operation()
            self.assertEqual(raised.exception.status, 401)
        with self.assertRaises(AuthError): self.answer(question, request_id=request)
        token, _ = self.auth.login("admin", "temporary-run-fixture-admin-123", "test")
        self.auth.sessions[self.auth._digest(token)] = replace(original, expires_at=0)
        for operation in operations(token):
            with self.assertRaises(AuthError) as raised: operation()
            self.assertEqual(raised.exception.status, 401)

    def test_expired_answer_commits_expired_state_and_cannot_wake_work(self):
        question = self.ask(); self.now += 2000
        request = str(uuid4())
        for repeat in range(2):
            with self.assertRaises(CollaborationError) as raised: self.answer(question, request_id=request)
            self.assertEqual((raised.exception.status, raised.exception.code), (409, "question_expired"))
        self.assertEqual(self.rows("SELECT status,revision FROM collab_decisions"), [{"status": "expired", "revision": 1}])
        self.assertEqual(self.rows("SELECT * FROM collab_answer_deliveries"), [])
        self.assertEqual(self.runs.get(self.run["id"])["wall_used_ms"], 2000)
        self.assertEqual(self.rows("SELECT status FROM collab_work_items")[0]["status"], "ready")

    def test_stale_policy_or_work_revision_does_not_apply_an_old_answer(self):
        question = self.ask()
        self.extend_limits({"wall_seconds": 11})
        with self.assertRaises(CollaborationError) as raised: self.answer(question)
        self.assertEqual(raised.exception.code, "question_stale")
        self.assertEqual(self.rows("SELECT status FROM collab_work_items")[0]["status"], "ready")
        self.actor = self.runs.bind_member(self.run["id"], self.actor.member_id)
        self.work_id, self.work_attempt_id = self.seed_work(self.actor)
        question = self.ask()
        with self.store.transaction() as db: db.execute("UPDATE collab_work_items SET revision=revision+1 WHERE id=?", (self.work_id,))
        with self.assertRaises(CollaborationError) as raised: self.answer(question)
        self.assertEqual(raised.exception.code, "question_stale")
        self.assertEqual(self.rows("SELECT status,revision FROM collab_work_items WHERE id=?", (self.work_id,)), [{"status":"waiting_user","revision":2}])
        self.assertEqual(self.rows("SELECT * FROM collab_answer_deliveries"), [])

    def test_stop_invalidates_pending_question_and_old_answer_cannot_wake_continue(self):
        question = self.ask()
        stopped = self.finish()
        self.assertEqual(self.rows("SELECT status FROM collab_decisions")[0]["status"], "cancelled")
        self.run = self.runs.continue_run(self.run["id"], str(uuid4()), expected_revision=stopped["revision"])
        self.activate()
        with self.assertRaises(CollaborationError) as raised: self.answer(question)
        self.assertEqual(raised.exception.code, "question_closed")
        self.assertEqual(self.rows("SELECT * FROM collab_answer_deliveries"), [])
        self.assertEqual(len(self.rows("SELECT * FROM collab_work_attempts")), 1)

    def test_parallel_different_answers_accept_only_one_immutable_result(self):
        question = self.ask(); barrier = Barrier(2)
        def answer(index):
            with CollaborationStore(self.path) as store:
                service = QuestionService(store, self.auth, self.workspace, clock_ms=lambda: self.now)
                barrier.wait(timeout=5)
                try:
                    return service.answer(self.token, self.run["id"], str(uuid4()), {"question_id": question["id"], "expected_revision": 0,
                        "answer": {"outcome": "answered", "text": "choice-"+str(index)}})
                except CollaborationError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(answer, range(2)))
        self.assertEqual(results.count("question_closed"), 1)
        self.assertEqual(len(self.rows("SELECT * FROM collab_decision_answers")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM collab_answer_deliveries")), 1)
        self.assertIn(self.rows("SELECT body FROM collab_decision_answers")[0]["body"], ("choice-0", "choice-1"))

    def test_answer_and_stop_race_never_accepts_an_answer_after_stop(self):
        question = self.ask(); barrier = Barrier(2)
        def race(action):
            with CollaborationStore(self.path) as store:
                barrier.wait(timeout=5)
                if action == "stop":
                    return RunRepository(store, self.scope, clock_ms=lambda: self.now).stop(self.run["id"], str(uuid4()), expected_revision=1)["status"]
                service = QuestionService(store, self.auth, self.workspace, clock_ms=lambda: self.now)
                try:
                    return service.answer(self.token, self.run["id"], str(uuid4()), {"question_id":question["id"],"expected_revision":0,
                        "answer":{"outcome":"answered","text":"accepted before stop"}})["status"]
                except CollaborationError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(race, ("stop", "answer")))
        self.assertIn("stopping", results)
        if "accepted" in results:
            self.assertEqual(self.rows("SELECT status FROM collab_decisions")[0]["status"], "answered")
            self.assertEqual(len(self.rows("SELECT * FROM collab_answer_deliveries")), 1)
        else:
            self.assertIn("question_closed", results)
            self.assertEqual(self.rows("SELECT status FROM collab_decisions")[0]["status"], "cancelled")
            self.assertEqual(self.rows("SELECT * FROM collab_answer_deliveries"), [])

    def test_answer_arriving_during_turn_is_queued_for_next_claim(self):
        self.mailbox.send(self.peer, str(uuid4()), {"recipient": self.actor.member_id, "kind":"finding", "body":"Inspect this"})
        batch = self.mailbox.claim(self.actor, str(uuid4()), work_attempt_id=self.work_attempt_id)
        question = self.ask(); response = self.answer(question)
        self.assertEqual(self.finish_batch(batch)["pending_messages"], 1)
        self.assertEqual(self.rows("SELECT status FROM collab_answer_deliveries")[0]["status"], "accepted")
        following = self.mailbox.claim(self.actor, str(uuid4()), work_attempt_id=self.work_attempt_id)
        self.assertEqual([row["id"] for row in following["answers"]], [response["id"]])
        self.assertEqual(self.finish_batch(following)["pending_messages"], 0)

    def test_messages_and_answers_share_sequence_and_batch_limit(self):
        self.mailbox.send(self.peer, str(uuid4()), {"recipient":self.actor.member_id,"kind":"status","body":"First"})
        question = self.ask(); self.answer(question)
        self.mailbox.send(self.peer, str(uuid4()), {"recipient":self.actor.member_id,"kind":"status","body":"Third"})
        first = self.mailbox.claim(self.actor, str(uuid4()), work_attempt_id=self.work_attempt_id, limit=2)
        self.assertEqual([row["sequence"] for row in first["messages"]], [1])
        self.assertEqual([row["sequence"] for row in first["answers"]], [2])
        self.assertEqual(self.finish_batch(first)["pending_messages"], 1)
        second = self.mailbox.claim(self.actor, str(uuid4()), work_attempt_id=self.work_attempt_id)
        self.assertEqual([row["sequence"] for row in second["messages"]], [3])
        self.assertEqual(second["answers"], [])

    def test_unknown_answer_turn_remains_delivered_and_cannot_be_replayed(self):
        question = self.ask(); self.answer(question)
        batch = self.mailbox.claim(self.actor, str(uuid4()), work_attempt_id=self.work_attempt_id)
        self.finish_batch(batch, outcome="interrupted")
        self.assertEqual(self.rows("SELECT status FROM collab_answer_deliveries")[0]["status"], "delivered")
        with self.assertRaises(CollaborationError) as raised:
            self.mailbox.claim(self.actor, str(uuid4()), work_attempt_id=self.work_attempt_id)
        self.assertEqual(raised.exception.code, "turn_pending")

    def test_decline_is_a_recorded_answer_and_does_not_auto_create_another_request(self):
        question = self.ask(); self.answer(question, outcome="declined", text="Replan within original scope")
        self.assertEqual(self.rows("SELECT status FROM collab_decisions")[0]["status"], "declined")
        self.assertEqual(self.rows("SELECT status FROM collab_work_attempts")[0]["status"], "running")
        self.assertEqual(len(self.rows("SELECT * FROM collab_decisions")), 1)
        with self.assertRaises(CollaborationError) as raised: self.ask()
        self.assertEqual(raised.exception.code, "answer_pending")
        batch = self.mailbox.claim(self.actor, str(uuid4()), work_attempt_id=self.work_attempt_id)
        self.assertEqual(batch["answers"][0]["outcome"], "declined")
        self.assertEqual(len(self.rows("SELECT * FROM collab_run_attempts")), 1)

    def test_forged_identity_and_cross_member_work_are_rejected(self):
        for value in ({"prompt":"P", "member_id":self.peer.member_id}, {"prompt":"P", "kind":"capability"},
                      {"prompt":"P", "expires_ms":999999}, {"prompt":"P", "policy_version":2}):
            with self.assertRaises(CollaborationError) as raised: self.ask(value)
            self.assertEqual(raised.exception.code, "invalid_fields")
        with self.assertRaises(CollaborationError) as raised:
            self.questions.ask(self.peer, self.work_attempt_id, str(uuid4()), {"prompt":"Stolen work"})
        self.assertEqual(raised.exception.code, "work_attempt_mismatch")
        with self.assertRaises(CollaborationError) as raised:
            self.questions.ask(replace(self.actor, member_id=self.peer.member_id), self.work_attempt_id, str(uuid4()), {"prompt":"Forged"})
        self.assertEqual(raised.exception.code, "invalid_binding")
        other_workspace = Path(self.temporary.name)/"other-workspace"; other_workspace.mkdir()
        outsider = QuestionService(self.store, self.auth, other_workspace)
        with self.assertRaises(CollaborationError) as raised: outsider.list_questions(self.token, self.run["id"])
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(self.rows("SELECT * FROM collab_decisions"), [])

    def test_pending_questions_paginate_expire_and_preserve_history_after_reopen(self):
        first = self.ask()
        _, another = self.seed_work(self.peer)
        second = self.questions.ask(self.peer, another, str(uuid4()), {"prompt":"Second question"})
        page = self.admin.list_questions(self.token, self.run["id"], limit=1)
        self.assertTrue(page["has_more"])
        self.assertEqual([row["id"] for row in page["items"]], [first["id"]])
        self.assertEqual([row["id"] for row in self.admin.list_questions(self.token, self.run["id"], after_sequence=page["next_cursor"])["items"]], [second["id"]])
        self.now += 2000
        expired = self.questions.expire_due(self.run["id"], str(uuid4()))
        self.assertEqual(set(expired["expired"]), {first["id"],second["id"]})
        with CollaborationStore(self.path) as store:
            admin = QuestionService(store, self.auth, self.workspace)
            self.assertEqual([row["status"] for row in admin.list_questions(self.token,self.run["id"])["items"]], ["expired","expired"])

    def test_known_secrets_are_redacted_and_different_raw_answers_conflict(self):
        self.auth.redactor.add("secret-question-fixture", "secret-answer-one", "secret-answer-two")
        question = self.ask({"prompt":"Please inspect secret-question-fixture"})
        self.assertEqual(question["question"]["prompt"], "Please inspect [REDACTED]")
        request = str(uuid4()); self.answer(question, text="secret-answer-one", request_id=request)
        with self.assertRaises(CollaborationError) as raised: self.answer(question,text="secret-answer-two",request_id=request)
        self.assertEqual(raised.exception.code,"request_conflict")
        self.assertEqual(self.admin.list_questions(self.token,self.run["id"])["items"][0]["answer"]["text"], "[REDACTED]")
        for table in ("collab_decisions","collab_decision_answers","collab_requests","collab_events"):
            contents = str(self.rows("SELECT * FROM "+table))
            for secret in ("secret-question-fixture", "secret-answer-one", "secret-answer-two"): self.assertNotIn(secret, contents)

    def test_answer_storage_failure_rolls_back_all_state_and_outbox_changes(self):
        question = self.ask()
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fixture_reject_answer BEFORE INSERT ON collab_answer_deliveries "
                       "BEGIN SELECT RAISE(ABORT,'injected answer failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected answer failure"): self.answer(question)
        self.assertEqual(self.rows("SELECT status,revision FROM collab_decisions"), [{"status":"pending","revision":0}])
        self.assertEqual(self.rows("SELECT status FROM collab_work_items")[0]["status"], "waiting_user")
        self.assertEqual(self.rows("SELECT * FROM collab_decision_answers"), [])
        self.assertEqual(self.rows("SELECT * FROM collab_mailbox_cursors"), [])
        self.assertEqual(self.rows("SELECT * FROM collab_requests WHERE action='answer_user_question'"), [])

    def test_invalid_question_and_answer_contracts_cannot_grant_authority(self):
        for value in ({}, {"prompt":""}, {"prompt":"x"*32001}, {"prompt":"Q", "options":["only"]},
                      {"prompt":"Q","options":["same","same"]}, {"prompt":"Q","options":"not-array"}):
            with self.subTest(value=value), self.assertRaises(CollaborationError) as raised: self.ask(value)
            self.assertEqual(raised.exception.status,400)
        question = self.ask()
        for answer in ({"outcome":"approved","text":"expand permissions"}, {"outcome":"answered","text":"","member_id":self.peer.member_id},
                       {"outcome":"answered","text":"x"*32001}):
            with self.subTest(answer=answer), self.assertRaises(CollaborationError) as raised:
                self.admin.answer(self.token,self.run["id"],str(uuid4()),{"question_id":question["id"],"answer":answer,"expected_revision":0})
            self.assertEqual(raised.exception.status,400)
        self.assertEqual(self.rows("SELECT status FROM collab_decisions")[0]["status"],"pending")

    def test_waiting_budget_expiry_cancels_question_before_dispatch_can_resume(self):
        question = self.ask(); self.now += 10_000
        with self.assertRaises(CollaborationError) as raised: self.answer(question)
        self.assertEqual(raised.exception.code,"question_closed")
        run = self.runs.get(self.run["id"])
        self.assertEqual((run["status"],run["terminal_intent"],run["wall_used_ms"]),("stopping","failed",10_000))
        self.assertEqual(self.rows("SELECT status FROM collab_decisions")[0]["status"],"cancelled")
        self.assertEqual(self.rows("SELECT * FROM collab_answer_deliveries"),[])

    def test_logout_revokes_interaction_but_not_existing_background_member_grant(self):
        question = self.ask(); self.auth.logout(self.token)
        with self.assertRaises(AuthError) as raised: self.answer(question)
        self.assertEqual(raised.exception.status,401)
        delivered = self.mailbox.send(self.peer,str(uuid4()),{"recipient":self.actor.member_id,"kind":"status","body":"Independent work continues"})
        self.assertEqual(delivered["status"],"accepted")
        self.assertEqual(self.runs.get(self.run["id"])["status"],"running")
        self.assertEqual(self.rows("SELECT status FROM collab_decisions")[0]["status"],"pending")

    def test_answer_foreign_run_and_workspace_are_denied_before_replay(self):
        question = self.ask(); request = str(uuid4()); self.answer(question,request_id=request)
        stopped = self.finish()
        other_team = self.service.create_team(self.token,str(uuid4()),self.config)
        other = self.runs.prepare(other_team["id"],str(uuid4()),expected_revision=0)
        value = {"question_id":question["id"],"expected_revision":0,"answer":{"outcome":"answered","text":"Use the documented behavior"}}
        with self.assertRaises(CollaborationError) as raised: self.admin.answer(self.token,other["id"],request,value)
        self.assertEqual((raised.exception.status,raised.exception.code),(403,"question_mismatch"))
        directory = Path(self.temporary.name)/"foreign-workspace"; directory.mkdir()
        outsider = QuestionService(self.store,self.auth,directory)
        with self.assertRaises(CollaborationError) as raised: outsider.answer(self.token,self.run["id"],request,value)
        self.assertEqual(raised.exception.status,403)
        self.assertEqual(self.runs.get(self.run["id"])["status"],stopped["status"])
        self.assertEqual(len(self.rows("SELECT * FROM collab_decision_answers")),1)

    def test_sql_guards_preserve_terminal_question_answer_and_active_work_identity(self):
        question = self.ask(); self.answer(question)
        for query in ("UPDATE collab_decisions SET status='pending',revision=revision+1",
                      "UPDATE collab_decisions SET member_id='forged'", "UPDATE collab_decision_answers SET body='altered'"):
            with self.subTest(query=query), self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as db: db.execute(query)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction() as db:
                db.execute("INSERT INTO collab_work_attempts(id,work_id,run_id,run_attempt_id,member_id,number,status,inputs_json) "
                           "VALUES (?,?,?,?,?,2,'running','{}')",(str(uuid4()),self.work_id,self.run["id"],self.actor.attempt_id,self.actor.member_id))
