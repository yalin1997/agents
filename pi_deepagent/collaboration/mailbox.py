"""Durable member mailboxes, with coordinator-only turn claims and outcomes.

send() is a future model-tool boundary: its binding comes from a tool closure,
never model arguments. claim()/finish_turn() belong to the trusted runtime;
they record delivery, not graph execution or evidence that work was successful.
Result references must already be registered by a trusted producer. Message
contents cannot alter policy, identities, verification results or scheduling.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import re
from uuid import uuid4

from .budgets import BudgetRepository
from .contracts import CollaborationError, FrozenJSON, integer, opaque_id, required_text, strict_fields
from .runs import RunBinding, RunRepository, conflict
from .storage import utc_now


class MailboxRepository:
    def __init__(self, runs: RunRepository, redactor):
        self.runs, self.store, self.scope = runs, runs.store, runs.scope
        self.budgets, self.redactor = BudgetRepository(runs), redactor

    def _actor(self, db, binding, *, allow_historical_policy=False):
        run = self.runs.authorize_binding(db, binding, allow_historical_policy=allow_historical_policy)
        if binding.actor != "member":
            raise CollaborationError(403, "member_required", "Only a run member may use this mailbox")
        return run

    @staticmethod
    def _binding(binding):
        if not isinstance(binding, RunBinding):
            raise CollaborationError(403, "invalid_binding", "A service-bound member is required")
        return asdict(binding)

    @staticmethod
    def _input(payload):
        strict_fields(payload, ("recipient", "kind", "body"), ("work_id", "reply_to", "artifact_refs"))
        body = required_text(payload["body"])
        if len(body) > 32000:
            raise CollaborationError(400, "message_too_long", "Message exceeds 32000 characters")
        if payload["kind"] not in ("question", "answer", "handoff", "finding", "status"):
            raise CollaborationError(400, "invalid_message_kind", "Unrecognized message kind")
        recipient = payload["recipient"] if payload["recipient"] == "team" else opaque_id(payload["recipient"])
        refs = payload.get("artifact_refs", [])
        if not isinstance(refs, list) or len(refs) > 100:
            raise CollaborationError(400, "invalid_artifact_refs", "Invalid result references")
        cleaned_refs = []
        for ref in refs:
            strict_fields(ref, ("id", "content_hash"))
            if not isinstance(ref["content_hash"], str) or not re.fullmatch(r"[0-9a-f]{64}", ref["content_hash"]):
                raise CollaborationError(400, "invalid_artifact_refs", "Result reference requires an exact content hash")
            cleaned_refs.append({"id": opaque_id(ref["id"]), "content_hash": ref["content_hash"]})
        if len({ref["id"] for ref in cleaned_refs}) != len(cleaned_refs):
            raise CollaborationError(400, "invalid_artifact_refs", "Result references must be unique")
        return {"recipient": recipient, "kind": payload["kind"], "body": body,
                "work_id": None if payload.get("work_id") is None else opaque_id(payload["work_id"]),
                "reply_to": None if payload.get("reply_to") is None else opaque_id(payload["reply_to"]),
                "artifact_refs": cleaned_refs}

    @staticmethod
    def _message(row):
        return {"id": row["id"], "run_id": row["run_id"], "attempt_id": row["attempt_id"],
                "sender": row["sender_id"], "recipients": json.loads(row["recipients_json"]),
                "work_id": row["work_id"], "reply_to": row["reply_to"], "kind": row["kind"],
                "body": row["body"], "artifact_refs": json.loads(row["artifact_refs_json"]), "created_at": row["created_at"]}

    @staticmethod
    def _answer(row):
        return {"id": row["id"], "question_id": row["decision_id"], "run_id": row["run_id"], "attempt_id": row["attempt_id"],
                "kind": "user_answer", "sender": {"type": "admin", "name": row["actor"]}, "recipient": row["member_id"],
                "work_id": row["work_id"], "work_attempt_id": row["work_attempt_id"], "body": row["body"], "outcome": row["outcome"],
                "sequence": row["sequence"], "created_at": row["created_at"], "status": "delivered"}

    def send(self, binding, request_id, payload):
        actor, value = self._binding(binding), self._input(payload)
        request_id = opaque_id(request_id)
        # Preserve exact-content idempotency without storing unredacted secrets
        # in the request ledger. Redactor changes cannot change replay identity.
        fingerprint = FrozenJSON(payload).sha256
        def operation(db):
            self._actor(db, binding)
            members = [row[0] for row in db.execute("SELECT id FROM collab_members WHERE run_id=? ORDER BY id", (binding.run_id,))]
            recipients = [member for member in members if member != binding.member_id] if value["recipient"] == "team" else [value["recipient"]]
            if any(member not in members or member == binding.member_id for member in recipients):
                raise CollaborationError(403, "recipient_mismatch", "Recipient must be a teammate in this run")
            if value["work_id"] is not None and not db.execute("SELECT 1 FROM collab_work_items WHERE id=? AND run_id=?",
                    (value["work_id"], binding.run_id)).fetchone():
                raise CollaborationError(403, "work_mismatch", "Work item does not belong to this run")
            if value["reply_to"] is not None:
                prior = db.execute("SELECT sender_id,recipients_json FROM collab_messages WHERE id=? AND run_id=?",
                                   (value["reply_to"], binding.run_id)).fetchone()
                if prior is None or binding.member_id not in [prior["sender_id"], *json.loads(prior["recipients_json"])]:
                    raise CollaborationError(403, "reply_mismatch", "Reply must reference a visible message in this run")
            for ref in value["artifact_refs"]:
                if not db.execute("SELECT 1 FROM collab_result_refs WHERE id=? AND run_id=? AND content_hash=?",
                                  (ref["id"], binding.run_id, ref["content_hash"])).fetchone():
                    raise CollaborationError(403, "artifact_mismatch", "Result reference does not match this run and content")
            body = self.redactor.clean(value["body"])
            if len(body) > 32000:
                raise CollaborationError(400, "message_too_long", "Redacted message exceeds 32000 characters")
            reservation = self.budgets.reserve_in_transaction(db, binding, request_id, "messages", 1)
            if "error" in reservation:
                return reservation
            identifier, created_at = str(uuid4()), utc_now()
            db.execute("INSERT INTO collab_messages VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (identifier, binding.run_id, binding.attempt_id, binding.member_id, FrozenJSON(recipients).json,
                        value["work_id"], value["reply_to"], value["kind"], body,
                        FrozenJSON(value["artifact_refs"]).json, created_at))
            for recipient in recipients:
                db.execute("INSERT INTO collab_mailbox_cursors VALUES (?,?,1) ON CONFLICT(run_id,member_id) "
                           "DO UPDATE SET last_sequence=last_sequence+1", (binding.run_id, recipient))
                sequence = db.execute("SELECT last_sequence FROM collab_mailbox_cursors WHERE run_id=? AND member_id=?",
                                      (binding.run_id, recipient)).fetchone()[0]
                db.execute("INSERT INTO collab_message_deliveries VALUES (?,?,?,?,'accepted',NULL)",
                           (identifier, binding.run_id, recipient, sequence))
            self.budgets.settle_in_transaction(db, binding, reservation["id"], certainty="known", started=True)
            self.store.event(db, self.scope, binding.run_id, "message_accepted", {"message_id": identifier, "recipients": recipients})
            row = db.execute("SELECT * FROM collab_messages WHERE id=?", (identifier,)).fetchone()
            return {**self._message(row), "status": "accepted"}
        result = self.store.mutate(self.scope, request_id, "send_message", binding.run_id,
            {"binding": actor, "input_hash": fingerprint}, authorize=lambda db: self._actor(db, binding), operation=operation)
        return self.budgets._raise_error(result)

    def inbox(self, binding, *, after_sequence=0, limit=50):
        self._binding(binding)
        integer(after_sequence, minimum=0, maximum=2**63-1); integer(limit, maximum=200)
        with self.store.transaction(write=False) as db:
            self._actor(db, binding)
            rows = db.execute("SELECT m.*,d.sequence,d.status FROM collab_message_deliveries d JOIN collab_messages m ON m.id=d.message_id "
                              "WHERE d.run_id=? AND d.recipient_id=? AND d.sequence>? ORDER BY d.sequence LIMIT ?",
                              (binding.run_id, binding.member_id, after_sequence, limit+1)).fetchall()
            selected = rows[:limit]
            return {"items": [{**self._message(row), "sequence": row["sequence"], "status": row["status"]} for row in selected],
                    "next_cursor": selected[-1]["sequence"] if selected else after_sequence, "has_more": len(rows)>limit}

    def claim(self, binding, request_id, *, limit=50, work_attempt_id=None):
        actor = self._binding(binding)
        integer(limit, maximum=200)
        if work_attempt_id is not None: work_attempt_id = opaque_id(work_attempt_id)
        def operation(db):
            run = self._actor(db, binding)
            if run["status"] != "running":
                return self.budgets._error(409, "dispatch_closed", "Run is not accepting another turn")
            if self.runs._wall_expired(db, binding.run_id):
                return self.budgets._error(429, "budget_exhausted", "Run wall-time budget is exhausted")
            if self.budgets.pending_for_actor(db, binding):
                conflict("actor_operation_unknown", "Actor must reconcile its operation before another turn")
            prior = db.execute("SELECT status,certainty FROM collab_member_turns WHERE run_id=? AND member_id=? AND attempt_id=? "
                               "AND (status IN ('queued','running') OR certainty='unknown') LIMIT 1",
                               (binding.run_id, binding.member_id, binding.attempt_id)).fetchone()
            if prior is not None:
                conflict("turn_pending", "Member has an active or unresolved turn")
            if work_attempt_id is not None and not db.execute("SELECT 1 FROM collab_work_attempts WHERE id=? AND run_id=? "
                    "AND run_attempt_id=? AND member_id=? AND status='running'",
                    (work_attempt_id, binding.run_id, binding.attempt_id, binding.member_id)).fetchone():
                raise CollaborationError(403, "work_attempt_mismatch", "Turn must belong to this member and active work attempt")
            rows = db.execute("SELECT m.*,d.sequence FROM collab_message_deliveries d JOIN collab_messages m ON m.id=d.message_id "
                              "WHERE d.run_id=? AND d.recipient_id=? AND d.status='accepted' AND m.attempt_id=? "
                              "ORDER BY d.sequence LIMIT ?", (binding.run_id, binding.member_id, binding.attempt_id, limit)).fetchall()
            answers = db.execute("SELECT a.*,q.attempt_id,q.member_id,q.work_id,q.work_attempt_id,d.sequence "
                                 "FROM collab_answer_deliveries d JOIN collab_decision_answers a ON a.id=d.answer_id "
                                 "JOIN collab_decisions q ON q.id=a.decision_id "
                                 "WHERE d.run_id=? AND d.member_id=? AND d.attempt_id=? AND d.work_attempt_id=? AND d.status='accepted' "
                                 "ORDER BY d.sequence LIMIT ?", (binding.run_id, binding.member_id, binding.attempt_id, work_attempt_id, limit)).fetchall()
            selected = sorted([(row["sequence"], "message", row) for row in rows] + [(row["sequence"], "answer", row) for row in answers],
                              key=lambda item: item[0])[:limit]
            rows = [item[2] for item in selected if item[1] == "message"]
            answers = [item[2] for item in selected if item[1] == "answer"]
            if not selected:
                return {"turn": None, "messages": [], "answers": []}
            identifier, fence = str(uuid4()), str(uuid4())
            db.execute("INSERT INTO collab_member_turns(id,run_id,attempt_id,member_id,work_attempt_id,binding_json,fence_id,status,created_at) "
                       "VALUES (?,?,?,?,?,?,?,'running',?)", (identifier, binding.run_id, binding.attempt_id, binding.member_id,
                       work_attempt_id, FrozenJSON(actor).json, fence, utc_now()))
            for row in rows:
                db.execute("UPDATE collab_message_deliveries SET status='delivered',turn_id=? WHERE message_id=? AND recipient_id=? AND status='accepted'",
                           (identifier, row["id"], binding.member_id))
            for row in answers:
                db.execute("UPDATE collab_answer_deliveries SET status='delivered',turn_id=? WHERE answer_id=? AND status='accepted'",
                           (identifier, row["id"]))
            self.store.event(db, self.scope, binding.run_id, "mailbox_batch_claimed",
                             {"turn_id": identifier, "member_id": binding.member_id, "message_ids": [row["id"] for row in rows],
                              "answer_ids": [row["id"] for row in answers]})
            return {"turn": {"id": identifier, "fence_id": fence, "revision": 0, "status": "running"},
                    "messages": [{**self._message(row), "sequence": row["sequence"], "status": "delivered"} for row in rows],
                    "answers": [self._answer(row) for row in answers]}
        result = self.store.mutate(self.scope, request_id, "claim_mailbox", binding.run_id,
            {"binding": actor, "limit": limit, "work_attempt_id": work_attempt_id}, authorize=lambda db: self._actor(db, binding), operation=operation)
        return self.budgets._raise_error(result)

    def finish_turn(self, binding, turn_id, request_id, *, fence_id, expected_revision, outcome):
        actor = self._binding(binding)
        turn_id, fence_id = opaque_id(turn_id), opaque_id(fence_id)
        integer(expected_revision, minimum=0)
        if outcome not in ("completed", "failed", "cancelled", "interrupted"):
            raise CollaborationError(400, "invalid_turn_outcome", "A trusted terminal turn outcome is required")
        def operation(db):
            self._actor(db, binding, allow_historical_policy=True)
            row = db.execute("SELECT * FROM collab_member_turns WHERE id=? AND run_id=?", (turn_id, binding.run_id)).fetchone()
            if row is None or row["binding_json"] != FrozenJSON(actor).json or row["fence_id"] != fence_id:
                raise CollaborationError(403, "turn_mismatch", "Turn does not belong to this bound runtime")
            if row["status"] != "running" or row["revision"] != expected_revision:
                conflict("stale_revision", "Turn has changed or already has an immutable outcome")
            pending = self.budgets.pending_for_actor(db, binding, include_reserved=True)
            if outcome != "interrupted" and pending:
                conflict("resources_not_drained", "Turn has outstanding or unknown operations")
            if outcome == "interrupted":
                for item in pending:
                    db.execute("UPDATE collab_budget_reservations SET status='unknown' WHERE id=?", (item["id"],))
            certainty = "unknown" if outcome == "interrupted" else "known"
            db.execute("UPDATE collab_member_turns SET status=?,certainty=?,revision=revision+1 WHERE id=?",
                       (outcome, certainty, turn_id))
            if outcome == "completed":
                db.execute("UPDATE collab_message_deliveries SET status='processed' WHERE turn_id=? AND status='delivered'", (turn_id,))
                db.execute("UPDATE collab_answer_deliveries SET status='processed' WHERE turn_id=? AND status='delivered'", (turn_id,))
            remaining = db.execute("SELECT COUNT(*) FROM collab_message_deliveries d JOIN collab_messages m ON m.id=d.message_id "
                                   "WHERE d.run_id=? AND d.recipient_id=? AND d.status!='processed' AND m.attempt_id=?",
                                   (binding.run_id, binding.member_id, binding.attempt_id)).fetchone()[0]
            remaining += db.execute("SELECT COUNT(*) FROM collab_answer_deliveries WHERE run_id=? AND member_id=? AND attempt_id=? AND status!='processed'",
                                    (binding.run_id, binding.member_id, binding.attempt_id)).fetchone()[0]
            self.store.event(db, self.scope, binding.run_id, "member_turn_finished",
                             {"turn_id": turn_id, "outcome": outcome, "pending_messages": remaining})
            return {"id": turn_id, "status": outcome, "certainty": certainty, "revision": expected_revision+1, "pending_messages": remaining}
        return self.store.mutate(self.scope, request_id, "finish_member_turn", binding.run_id,
            {"binding": actor, "turn_id": turn_id, "fence_id": fence_id, "expected_revision": expected_revision, "outcome": outcome},
            authorize=lambda db: self._actor(db, binding, allow_historical_policy=True), operation=operation)
