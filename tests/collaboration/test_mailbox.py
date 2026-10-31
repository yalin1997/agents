"""Real SQLite delivery and actor boundaries; no graph execution is simulated."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
from threading import Barrier
from uuid import uuid4

from pi_deepagent.collaboration.contracts import CollaborationError, FrozenJSON
from pi_deepagent.collaboration.mailbox import MailboxRepository
from pi_deepagent.collaboration.runs import RunRepository
from pi_deepagent.collaboration.storage import CollaborationStore, Scope
from tests.collaboration.test_runs import RunFixture


class MailboxTests(RunFixture):
    def setUp(self):
        super().setUp()
        self.lead = self.activate()
        self.builder, self.verifier = [self.runs.bind_member(self.run["id"], row["id"]) for row in self.run["members"][1:]]
        self.mailbox = MailboxRepository(self.runs, self.auth.redactor)

    def send(self, sender=None, recipient=None, body="Observed a real defect", **extra):
        return self.mailbox.send(sender or self.verifier, str(uuid4()),
            {"recipient": recipient or self.builder.member_id, "kind": "finding", "body": body, **extra})

    def finish_batch(self, batch, actor=None, outcome="completed", request_id=None):
        turn = batch["turn"]
        return self.mailbox.finish_turn(actor or self.builder, turn["id"], request_id or str(uuid4()),
            fence_id=turn["fence_id"], expected_revision=turn["revision"], outcome=outcome)

    def test_peer_delivery_reply_and_outcome_have_a_persistent_chain(self):
        first = self.send()
        self.assertEqual((first["sender"], first["recipients"], first["status"]),
                         (self.verifier.member_id, [self.builder.member_id], "accepted"))
        self.assertEqual(self.mailbox.inbox(self.lead)["items"], [])
        batch = self.mailbox.claim(self.builder, str(uuid4()))
        self.assertEqual([item["id"] for item in batch["messages"]], [first["id"]])
        self.assertEqual(self.mailbox.inbox(self.builder)["items"][0]["status"], "delivered")
        completed = self.finish_batch(batch)
        self.assertEqual((completed["status"], completed["certainty"], completed["pending_messages"]), ("completed", "known", 0))
        reply = self.send(self.builder, self.verifier.member_id, "Repaired; please verify", kind="answer", reply_to=first["id"])
        self.assertEqual(self.mailbox.inbox(self.verifier)["items"][0]["reply_to"], first["id"])
        with CollaborationStore(self.path) as store:
            mailbox = MailboxRepository(RunRepository(store, self.scope), self.auth.redactor)
            self.assertEqual(mailbox.inbox(self.builder)["items"][0]["status"], "processed")
            self.assertEqual(mailbox.inbox(self.verifier)["items"][0]["id"], reply["id"])
        self.assertEqual(self.runs.get(self.run["id"])["budgets"]["messages"], {"used": 2, "reserved": 0})

    def test_broadcast_replay_keeps_recipients_and_per_member_sequence_without_recharging(self):
        request = str(uuid4())
        payload = {"recipient": "team", "kind": "status", "body": "Shared context"}
        first = self.mailbox.send(self.lead, request, payload)
        self.assertEqual(set(first["recipients"]), {self.builder.member_id, self.verifier.member_id})
        self.assertEqual(self.mailbox.send(self.lead, request, payload), first)
        self.send(self.verifier, self.builder.member_id, "Second item")
        first_page = self.mailbox.inbox(self.builder, limit=1)
        self.assertTrue(first_page["has_more"])
        self.assertEqual(first_page["items"][0]["sequence"], 1)
        self.assertEqual(self.mailbox.inbox(self.builder, after_sequence=first_page["next_cursor"])["items"][0]["sequence"], 2)
        self.assertEqual([item["sequence"] for item in self.mailbox.inbox(self.verifier)["items"]], [1])
        self.assertEqual(self.runs.get(self.run["id"])["budgets"]["messages"], {"used": 2, "reserved": 0})
        with self.assertRaises(CollaborationError) as raised:
            self.mailbox.send(self.lead, request, {**payload, "body": "Changed"})
        self.assertEqual(raised.exception.code, "request_conflict")

    def test_new_arrival_during_turn_remains_queued_and_is_claimed_next(self):
        first = self.send(body="First")
        request = str(uuid4())
        batch = self.mailbox.claim(self.builder, request)
        second = self.send(body="Arrived while running")
        self.assertEqual(self.mailbox.claim(self.builder, request), batch)
        with self.assertRaises(CollaborationError) as raised: self.mailbox.claim(self.builder, str(uuid4()))
        self.assertEqual(raised.exception.code, "turn_pending")
        result = self.finish_batch(batch)
        self.assertEqual(result["pending_messages"], 1)
        items = self.mailbox.inbox(self.builder)["items"]
        self.assertEqual([(item["id"], item["status"]) for item in items], [(first["id"], "processed"), (second["id"], "accepted")])
        following = self.mailbox.claim(self.builder, str(uuid4()))
        self.assertEqual([item["id"] for item in following["messages"]], [second["id"]])
        self.assertEqual(self.finish_batch(following)["pending_messages"], 0)

    def test_two_connections_claim_only_one_turn_and_batch(self):
        message = self.send()
        barrier = Barrier(2)
        def claim(index):
            with CollaborationStore(self.path) as store:
                mailbox = MailboxRepository(RunRepository(store, self.scope, clock_ms=lambda: self.now), self.auth.redactor)
                barrier.wait(timeout=5)
                try: return mailbox.claim(self.builder, str(uuid4()))
                except CollaborationError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(claim, range(2)))
        self.assertEqual(results.count("turn_pending"), 1)
        winner = next(result for result in results if isinstance(result, dict))
        self.assertEqual([row["id"] for row in winner["messages"]], [message["id"]])
        with self.store.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_member_turns").fetchone()[0], 1)

    def test_parallel_identical_send_commits_once_including_budget_and_cursor(self):
        barrier, request = Barrier(2), str(uuid4())
        payload = {"recipient": self.builder.member_id, "kind": "question", "body": "Can you inspect?"}
        def send(index):
            with CollaborationStore(self.path) as store:
                mailbox = MailboxRepository(RunRepository(store, self.scope, clock_ms=lambda: self.now), self.auth.redactor)
                barrier.wait(timeout=5)
                return mailbox.send(self.verifier, request, payload)
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(send, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.mailbox.inbox(self.builder)["items"]), 1)
        self.assertEqual(self.runs.get(self.run["id"])["budgets"]["messages"], {"used": 1, "reserved": 0})
        with self.store.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT last_sequence FROM collab_mailbox_cursors").fetchone()[0], 1)

    def test_unknown_turn_is_not_processed_replayed_or_released_by_stop(self):
        self.send(); batch = self.mailbox.claim(self.builder, str(uuid4()))
        reserved = self.budgets.reserve(self.builder, str(uuid4()), "model_calls")
        unknown = self.finish_batch(batch, outcome="interrupted")
        self.assertEqual((unknown["certainty"], unknown["pending_messages"]), ("unknown", 1))
        renewed = self.runs.bind_member(self.builder.run_id, self.builder.member_id)
        with self.assertRaises(CollaborationError) as raised: self.mailbox.claim(renewed, str(uuid4()))
        self.assertEqual(raised.exception.code, "actor_operation_unknown")
        self.assertEqual(self.mailbox.inbox(self.builder)["items"][0]["status"], "delivered")
        with self.store.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT status FROM collab_budget_reservations WHERE id=?", (reserved["id"],)).fetchone()[0], "unknown")
        run = self.runs.get(self.run["id"])
        self.runs.stop(run["id"], str(uuid4()), expected_revision=run["revision"])
        with self.assertRaises(CollaborationError) as raised:
            self.runs.finish_stopping(run["id"], str(uuid4()), attempt_id=run["attempt"]["id"])
        self.assertEqual(raised.exception.code, "resources_not_drained")

    def test_restart_marks_claimed_turn_interrupted_without_redelivery(self):
        self.send(); self.mailbox.claim(self.builder, str(uuid4()))
        interrupted = self.runs.mark_interrupted(self.run["id"], str(uuid4()), attempt_id=self.builder.attempt_id)
        self.assertEqual(interrupted["status"], "interrupted")
        with CollaborationStore(self.path) as store, store.transaction(write=False) as db:
            self.assertEqual(tuple(db.execute("SELECT status,certainty FROM collab_member_turns").fetchone()), ("interrupted", "unknown"))
            self.assertEqual(db.execute("SELECT status FROM collab_message_deliveries").fetchone()[0], "delivered")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_activity").fetchone()[0], 1)
        with self.assertRaises(CollaborationError): self.mailbox.claim(self.builder, str(uuid4()))

    def test_stopping_waits_for_turn_then_known_drain_keeps_late_accepted_message(self):
        self.send(); batch = self.mailbox.claim(self.builder, str(uuid4()))
        self.send(body="Queued handoff")
        run = self.runs.get(self.run["id"])
        self.runs.stop(run["id"], str(uuid4()), expected_revision=run["revision"])
        with self.assertRaises(CollaborationError) as raised:
            self.runs.finish_stopping(run["id"], str(uuid4()), attempt_id=self.builder.attempt_id)
        self.assertEqual(raised.exception.code, "resources_not_drained")
        self.assertEqual(self.finish_batch(batch)["pending_messages"], 1)
        stopped = self.runs.finish_stopping(run["id"], str(uuid4()), attempt_id=self.builder.attempt_id)
        self.run = self.runs.continue_run(run["id"], str(uuid4()), expected_revision=stopped["revision"])
        self.activate()
        renewed = self.runs.bind_member(self.builder.run_id, self.builder.member_id)
        self.assertEqual(self.mailbox.claim(renewed, str(uuid4())), {"turn": None, "messages": [], "answers": []})
        self.assertEqual([row["status"] for row in self.mailbox.inbox(renewed)["items"]], ["processed", "accepted"])

    def test_forged_sender_cross_scope_and_revoked_replay_are_denied(self):
        payload = {"recipient": self.builder.member_id, "kind": "status", "body": "Context"}
        with self.assertRaises(CollaborationError) as raised:
            self.mailbox.send(self.verifier, str(uuid4()), {**payload, "sender": self.lead.member_id})
        self.assertEqual(raised.exception.code, "invalid_fields")
        for actor in (None, replace(self.verifier, member_id=self.lead.member_id)):
            with self.assertRaises(CollaborationError) as raised: self.mailbox.send(actor, str(uuid4()), payload)
            self.assertEqual(raised.exception.status, 403)
        for scope in (Scope("other", self.scope.workspace), Scope("admin", "other-workspace")):
            mailbox = MailboxRepository(RunRepository(self.store, scope), self.auth.redactor)
            with self.assertRaises(CollaborationError) as raised: mailbox.inbox(self.verifier)
            self.assertEqual(raised.exception.status, 403)
        request = str(uuid4()); self.mailbox.send(self.verifier, request, payload)
        with self.store.transaction() as db: db.execute("UPDATE collab_actor_grants SET active=0 WHERE id=?", (self.verifier.grant_id,))
        with self.assertRaises(CollaborationError) as raised: self.mailbox.send(self.verifier, request, payload)
        self.assertEqual(raised.exception.status, 403)

    def test_cross_run_references_and_foreign_recipient_do_not_consume_budget(self):
        for extra, code in (({"recipient": str(uuid4())}, "recipient_mismatch"),
                            ({"reply_to": str(uuid4())}, "reply_mismatch"),
                            ({"work_id": str(uuid4())}, "work_mismatch"),
                            ({"artifact_refs": [{"id": str(uuid4()), "content_hash": "a"*64}]}, "artifact_mismatch")):
            with self.subTest(extra=extra), self.assertRaises(CollaborationError) as raised: self.send(**extra)
            self.assertEqual(raised.exception.code, code)
        self.assertEqual(self.runs.get(self.run["id"])["budgets"]["messages"], {"used": 0, "reserved": 0})

    def test_registered_work_and_result_hash_are_preserved_as_data(self):
        work_id, result_id = str(uuid4()), str(uuid4())
        with self.store.transaction() as db:
            db.execute("INSERT INTO collab_work_items(id,run_id,repair_root_id,goal,criteria_json,required,status) VALUES (?,?,?,?,?,1,'ready')",
                       (work_id, self.run["id"], work_id, "Inspect", '["evidence"]'))
            db.execute("INSERT INTO collab_result_refs VALUES (?,?,'change_set',?,'fixture-time')", (result_id, self.run["id"], "b"*64))
        refs = [{"id": result_id, "content_hash": "b"*64}]
        message = self.send(work_id=work_id, artifact_refs=refs)
        self.assertEqual((message["work_id"], message["artifact_refs"]), (work_id, refs))
        with self.assertRaises(CollaborationError) as raised:
            self.send(artifact_refs=[{"id": result_id, "content_hash": "c"*64}])
        self.assertEqual(raised.exception.code, "artifact_mismatch")

    def test_existing_foreign_run_objects_cannot_be_used_as_local_references(self):
        original = self.finish()
        other_team = self.service.create_team(self.token, str(uuid4()), self.config)
        other = self.runs.prepare(other_team["id"], str(uuid4()), expected_revision=0)
        other = self.runs.claim_start(other["id"], str(uuid4()), expected_revision=0)
        sender = self.runs.bind_member(other["id"], other["members"][0]["id"])
        recipient = other["members"][1]["id"]
        message = self.mailbox.send(sender, str(uuid4()), {"recipient": recipient, "kind": "status", "body": "Foreign run context"})
        work_id, result_id = str(uuid4()), str(uuid4())
        with self.store.transaction() as db:
            db.execute("INSERT INTO collab_work_items(id,run_id,repair_root_id,goal,criteria_json,required,status) VALUES (?,?,?,?,?,1,'ready')",
                       (work_id, other["id"], work_id, "Foreign work", '["Foreign work criterion"]'))
            db.execute("INSERT INTO collab_result_refs VALUES (?,?,'candidate',?,'fixture-time')", (result_id, other["id"], "a"*64))
        self.runs.stop(other["id"], str(uuid4()), expected_revision=other["revision"])
        self.runs.finish_stopping(other["id"], str(uuid4()), attempt_id=other["attempt"]["id"])
        self.run = self.runs.continue_run(original["id"], str(uuid4()), expected_revision=original["revision"])
        self.lead = self.activate()
        self.builder = self.runs.bind_member(self.run["id"], self.builder.member_id)
        self.verifier = self.runs.bind_member(self.run["id"], self.verifier.member_id)
        for extra, code in (({"recipient": recipient}, "recipient_mismatch"), ({"reply_to": message["id"]}, "reply_mismatch"),
                            ({"work_id": work_id}, "work_mismatch"),
                            ({"artifact_refs": [{"id": result_id, "content_hash": "a"*64}]}, "artifact_mismatch")):
            with self.subTest(extra=extra), self.assertRaises(CollaborationError) as raised: self.send(**extra)
            self.assertEqual((raised.exception.status, raised.exception.code), (403, code))
        with self.assertRaises(CollaborationError) as raised:
            self.mailbox.send(replace(self.verifier, run_id=other["id"]), str(uuid4()),
                              {"recipient": recipient, "kind": "status", "body": "Spoofed run"})
        self.assertEqual(raised.exception.code, "invalid_binding")
        self.assertEqual(self.runs.get(self.run["id"])["budgets"]["messages"], {"used": 0, "reserved": 0})

    def test_secret_redaction_does_not_collapse_exact_content_idempotency(self):
        secrets = ("known-fixture-secret-one", "known-fixture-secret-two")
        self.auth.redactor.add(*secrets)
        request = str(uuid4())
        payload = {"recipient": self.builder.member_id, "kind": "status", "body": secrets[0]}
        accepted = self.mailbox.send(self.verifier, request, payload)
        self.assertEqual(accepted["body"], "[REDACTED]")
        self.assertEqual(self.mailbox.send(self.verifier, request, payload), accepted)
        with self.assertRaises(CollaborationError) as raised:
            self.mailbox.send(self.verifier, request, {**payload, "body": secrets[1]})
        self.assertEqual(raised.exception.code, "request_conflict")
        with self.store.transaction(write=False) as db:
            for table in ("collab_messages", "collab_events", "collab_requests"):
                contents = str([tuple(row) for row in db.execute("SELECT * FROM " + table)])
                for secret in secrets: self.assertNotIn(secret, contents)

    def test_redaction_expansion_returns_safe_length_error_without_spending_budget(self):
        self.auth.redactor.add("xyz")
        with self.assertRaises(CollaborationError) as raised: self.send(body="xyz"*10666)
        self.assertEqual((raised.exception.status, raised.exception.code), (400, "message_too_long"))
        self.assertNotIn("xyz", raised.exception.message)
        self.assertEqual(self.runs.get(self.run["id"])["budgets"]["messages"], {"used": 0, "reserved": 0})
        self.assertEqual(self.mailbox.inbox(self.builder)["items"], [])

    def test_message_limit_counts_broadcast_once_and_exhaustion_commits_stop(self):
        for index in range(3): self.send(self.lead, "team", body=str(index))
        with self.assertRaises(CollaborationError) as raised: self.send(self.lead, "team", body="Excess")
        self.assertEqual((raised.exception.status, raised.exception.code), (429, "budget_exhausted"))
        run = self.runs.get(self.run["id"])
        self.assertEqual((run["status"], run["terminal_intent"], run["budgets"]["messages"]),
                         ("stopping", "failed", {"used": 3, "reserved": 0}))
        self.assertEqual(len(self.mailbox.inbox(self.builder)["items"]), 3)
        with self.store.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_message_deliveries").fetchone()[0], 6)

    def test_turn_cannot_finish_while_operation_unknown_and_outcome_is_immutable(self):
        self.send(); batch = self.mailbox.claim(self.builder, str(uuid4()))
        pending = self.budgets.reserve(self.builder, str(uuid4()), "model_calls")
        with self.assertRaises(CollaborationError) as raised: self.finish_batch(batch)
        self.assertEqual(raised.exception.code, "resources_not_drained")
        self.budgets.settle(self.builder, pending["id"], str(uuid4()), certainty="known", started=True)
        request = str(uuid4()); finished = self.finish_batch(batch, request_id=request)
        self.assertEqual(self.finish_batch(batch, request_id=request), finished)
        with self.assertRaises(CollaborationError) as raised: self.finish_batch(batch, outcome="failed")
        self.assertEqual(raised.exception.code, "stale_revision")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction() as db: db.execute("UPDATE collab_member_turns SET status='running',certainty=NULL")

    def test_invalid_fields_lengths_types_and_fences_are_rejected(self):
        for extra in ({"body": ""}, {"body": "x"*32001}, {"kind": "grant_permission"},
                      {"artifact_refs": "invalid"}, {"artifact_refs": [{"id": str(uuid4()), "content_hash": "bad"}]},
                      {"policy_version": 2}):
            with self.subTest(extra=extra), self.assertRaises(CollaborationError) as raised: self.send(**extra)
            self.assertEqual(raised.exception.status, 400)
        self.send(body="漢"*32000)
        batch = self.mailbox.claim(self.builder, str(uuid4()))
        with self.assertRaises(CollaborationError) as raised:
            self.mailbox.finish_turn(self.builder, batch["turn"]["id"], str(uuid4()), fence_id=str(uuid4()), expected_revision=0, outcome="completed")
        self.assertEqual(raised.exception.code, "turn_mismatch")
        with self.assertRaises(CollaborationError) as raised: self.finish_batch(batch, actor=self.lead)
        self.assertEqual(raised.exception.code, "turn_mismatch")

    def test_storage_failure_rolls_back_message_cursor_budget_event_and_request(self):
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fixture_reject_delivery BEFORE INSERT ON collab_message_deliveries "
                       "BEGIN SELECT RAISE(ABORT,'injected storage failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected storage failure"): self.send()
        self.assertEqual(self.runs.get(self.run["id"])["budgets"]["messages"], {"used": 0, "reserved": 0})
        with self.store.transaction(write=False) as db:
            for table in ("collab_messages", "collab_mailbox_cursors", "collab_budget_reservations"):
                self.assertEqual(db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_requests WHERE action='send_message'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_events WHERE kind='message_accepted'").fetchone()[0], 0)

    def test_unknown_operation_blocks_turn_even_without_a_previous_turn(self):
        self.send()
        reservation = self.budgets.reserve(self.builder, str(uuid4()), "model_calls")
        self.budgets.settle(self.builder, reservation["id"], str(uuid4()), certainty="unknown", started=True)
        with self.assertRaises(CollaborationError) as raised: self.mailbox.claim(self.builder, str(uuid4()))
        self.assertEqual(raised.exception.code, "actor_operation_unknown")
        with self.store.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_member_turns").fetchone()[0], 0)

    def test_claim_at_wall_limit_commits_stop_without_delivering_messages(self):
        self.send(); self.now += 10_000
        with self.assertRaises(CollaborationError) as raised: self.mailbox.claim(self.builder, str(uuid4()))
        self.assertEqual(raised.exception.code, "budget_exhausted")
        self.assertEqual(self.runs.get(self.run["id"])["status"], "stopping")
        self.assertEqual(self.mailbox.inbox(self.builder)["items"][0]["status"], "accepted")

    def test_turn_work_attempt_must_be_current_and_assigned_to_recipient(self):
        self.send()
        with self.assertRaises(CollaborationError) as raised:
            self.mailbox.claim(self.builder, str(uuid4()), work_attempt_id=str(uuid4()))
        self.assertEqual(raised.exception.code, "work_attempt_mismatch")
        with self.assertRaises(CollaborationError) as raised:
            self.finish_batch({"turn": {"id": str(uuid4()), "fence_id": str(uuid4()), "revision": 0}}, outcome="verify_passed")
        self.assertEqual(raised.exception.code, "invalid_turn_outcome")
