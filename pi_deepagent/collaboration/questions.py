"""Typed user questions and authenticated answers, without starting execution.

Question tools receive a member binding and work attempt from their runtime
closure. Only prompt/options are model arguments. An answer is immutable data
for that original work attempt; it grants no capability or validation success.
Non-code acceptance, approvals and manual validation use separate contracts.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from uuid import uuid4

from .access import AdminAccess
from .budgets import BudgetRepository
from .contracts import CollaborationError, FrozenJSON, integer, opaque_id, required_text, strict_fields
from .runs import RunBinding, RunRepository, conflict
from .storage import utc_now


def _bounded_text(value):
    required_text(value)
    if len(value) > 32000:
        raise CollaborationError(400, "text_too_long", "Question or answer exceeds 32000 characters")
    return value


class QuestionRepository:
    def __init__(self, runs, redactor, *, ttl_seconds=1800):
        self.runs, self.store, self.scope = runs, runs.store, runs.scope
        self.redactor, self.budgets = redactor, BudgetRepository(runs)
        self.ttl_ms = integer(ttl_seconds, maximum=86400)*1000

    def _work(self, db, binding, work_attempt_id):
        run = self.runs.authorize_binding(db, binding)
        if binding.actor != "member":
            raise CollaborationError(403, "member_required", "Questions must originate from a member")
        attempt = db.execute("SELECT * FROM collab_work_attempts WHERE id=? AND run_id=? AND run_attempt_id=? AND member_id=?",
            (work_attempt_id, binding.run_id, binding.attempt_id, binding.member_id)).fetchone()
        if attempt is None:
            raise CollaborationError(403, "work_attempt_mismatch", "Work attempt is not bound to this member and run attempt")
        work = db.execute("SELECT * FROM collab_work_items WHERE id=? AND run_id=?", (attempt["work_id"], binding.run_id)).fetchone()
        return run, attempt, work

    @staticmethod
    def _question(db, run_id, question_id):
        row = db.execute("SELECT * FROM collab_decisions WHERE id=?", (question_id,)).fetchone()
        if row is None:
            raise CollaborationError(404, "question_not_found", "Question was not found")
        if row["run_id"] != run_id:
            raise CollaborationError(403, "question_mismatch", "Question does not belong to this run")
        return row

    @staticmethod
    def _dto(row):
        response = None
        if "answer_id" in row.keys() and row["answer_id"] is not None:
            response = {"id": row["answer_id"], "actor": row["answer_actor"], "outcome": row["answer_outcome"],
                        "text": row["answer_body"], "created_at": row["answered_at"]}
        return {key: row[key] for key in ("id", "run_id", "attempt_id", "member_id", "work_id", "work_attempt_id", "kind",
                "policy_version", "status", "revision", "expires_ms", "created_at", "sequence")} | {"question": json.loads(row["body_json"]), "answer": response}

    def _input(self, value):
        strict_fields(value, ("prompt",), ("options",))
        prompt = _bounded_text(value["prompt"])
        options = value.get("options", [])
        if not isinstance(options, list) or (options and not 2 <= len(options) <= 10):
            raise CollaborationError(400, "invalid_options", "Provide two to ten choices or omit them")
        options = [_bounded_text(item) for item in options]
        if len(set(options)) != len(options) or len(prompt)+sum(map(len, options)) > 32000:
            raise CollaborationError(400, "invalid_options", "Question choices must be distinct and fit the text limit")
        cleaned = {"prompt": self.redactor.clean(prompt), "options": [self.redactor.clean(item) for item in options]}
        if len(set(cleaned["options"])) != len(options) or len(cleaned["prompt"])+sum(map(len, cleaned["options"])) > 32000:
            raise CollaborationError(400, "invalid_options", "Redacted question must fit the text and choice limits")
        return cleaned

    def ask(self, binding, work_attempt_id, request_id, value):
        if not isinstance(binding, RunBinding):
            raise CollaborationError(403, "invalid_binding", "A runtime-bound member is required")
        work_attempt_id, request_id = opaque_id(work_attempt_id), opaque_id(request_id)
        cleaned, fingerprint = self._input(value), FrozenJSON(value).sha256
        def operation(db):
            run, attempt, work = self._work(db, binding, work_attempt_id)
            if run["status"] != "running":
                return self.budgets._error(409, "dispatch_closed", "Run is not accepting a question")
            if db.execute("SELECT 1 FROM collab_decisions WHERE work_attempt_id=? AND status='pending'", (work_attempt_id,)).fetchone():
                conflict("question_pending", "Work attempt already has a pending question")
            if db.execute("SELECT 1 FROM collab_answer_deliveries WHERE work_attempt_id=? AND status!='processed'", (work_attempt_id,)).fetchone():
                conflict("answer_pending", "Process the existing answer before another question")
            if attempt["status"] != "running" or work["status"] not in ("running", "ready"):
                conflict("work_not_running", "Only an active work attempt may ask a question")
            reservation = self.budgets.reserve_in_transaction(db, binding, request_id, "messages", 1)
            if "error" in reservation:
                return reservation
            now = integer(self.runs.clock_ms(), minimum=0, maximum=2**63-1)
            identifier = str(uuid4())
            # Waiting for an answer keeps this WorkAttempt, and releases no
            # external resource by itself. Runtime performs its own turn drain.
            db.execute("UPDATE collab_work_items SET status='waiting_user',revision=revision+1 WHERE id=? AND revision=?",
                       (work["id"], work["revision"]))
            db.execute("INSERT INTO collab_decisions(id,run_id,attempt_id,member_id,work_id,work_attempt_id,kind,policy_version,"
                       "work_revision,work_attempt_revision,body_json,status,expires_ms,created_at) "
                       "VALUES (?,?,?,?,?,?,'user_question',?,?,?,?,'pending',?,?)",
                       (identifier, binding.run_id, binding.attempt_id, binding.member_id, work["id"], work_attempt_id,
                        run["policy_version"], work["revision"]+1, attempt["revision"], FrozenJSON(cleaned).json, now+self.ttl_ms, utc_now()))
            self.budgets.settle_in_transaction(db, binding, reservation["id"], certainty="known", started=True)
            self.store.event(db, self.scope, binding.run_id, "user_question_created",
                             {"question_id": identifier, "member_id": binding.member_id, "work_attempt_id": work_attempt_id})
            return self._dto(self._question(db, binding.run_id, identifier))
        result = self.store.mutate(self.scope, request_id, "ask_user", binding.run_id,
            {"binding": asdict(binding), "work_attempt_id": work_attempt_id, "input_hash": fingerprint},
            authorize=lambda db: self._work(db, binding, work_attempt_id), operation=operation)
        return self.budgets._raise_error(result)

    def _close(self, db, row, status):
        db.execute("UPDATE collab_decisions SET status=?,revision=revision+1 WHERE id=? AND status='pending'", (status, row["id"]))
        self.store.event(db, self.scope, row["run_id"], "user_question_"+status, {"question_id": row["id"]})

    def _restore_work(self, db, row):
        # A stale answer must not edit a reassigned or otherwise revised work.
        if not self._target_current(db, row):
            return
        db.execute("UPDATE collab_work_items SET status='ready',revision=revision+1 "
                   "WHERE id=? AND run_id=? AND revision=? AND status='waiting_user'",
                   (row["work_id"], row["run_id"], row["work_revision"]))

    @staticmethod
    def _target_current(db, row):
        work = db.execute("SELECT status,revision FROM collab_work_items WHERE id=? AND run_id=?", (row["work_id"], row["run_id"])).fetchone()
        attempt = db.execute("SELECT status,revision FROM collab_work_attempts WHERE id=? AND run_id=?",
                             (row["work_attempt_id"], row["run_id"])).fetchone()
        return (work["status"] == "waiting_user" and work["revision"] == row["work_revision"]
                and attempt["status"] == "running" and attempt["revision"] == row["work_attempt_revision"])

    def answer(self, run_id, request_id, value, *, authorize):
        run_id = opaque_id(run_id)
        strict_fields(value, ("question_id", "answer", "expected_revision"))
        question_id = opaque_id(value["question_id"])
        integer(value["expected_revision"], minimum=0)
        answer = strict_fields(value["answer"], ("outcome", "text"))
        if answer["outcome"] not in ("answered", "declined"):
            raise CollaborationError(400, "invalid_answer", "A user answer or explicit decline is required")
        body = _bounded_text(self.redactor.clean(_bounded_text(answer["text"])))
        def authenticated(db):
            authorize(db)
            self.runs._run(db, run_id)
            self._question(db, run_id, question_id)
        def operation(db):
            run = self.runs._run(db, run_id)
            row = self._question(db, run_id, question_id)
            if row["status"] != "pending" or row["revision"] != value["expected_revision"]:
                conflict("question_closed", "Question was answered, expired or superseded")
            if run["status"] != "running" or run["current_attempt_id"] != row["attempt_id"]:
                self._close(db, row, "cancelled")
                return self.budgets._error(409, "question_closed", "Question belongs to an inactive attempt")
            if self.runs._wall_expired(db, run_id):
                return self.budgets._error(409, "question_closed", "Run budget closed this question")
            if self.runs.clock_ms() >= row["expires_ms"]:
                self._close(db, row, "expired"); self._restore_work(db, row)
                return self.budgets._error(409, "question_expired", "Question has expired")
            if row["policy_version"] != run["policy_version"] or not self._target_current(db, row):
                self._close(db, row, "superseded")
                self._restore_work(db, row)
                return self.budgets._error(409, "question_stale", "Question target or policy has changed")
            self._close(db, row, answer["outcome"])
            self._restore_work(db, row)
            answer_id = str(uuid4())
            db.execute("INSERT INTO collab_decision_answers VALUES (?,?,?,'admin',?,?,?)",
                       (answer_id, question_id, run_id, answer["outcome"], body, utc_now()))
            db.execute("INSERT INTO collab_mailbox_cursors VALUES (?,?,1) ON CONFLICT(run_id,member_id) "
                       "DO UPDATE SET last_sequence=last_sequence+1", (run_id, row["member_id"]))
            sequence = db.execute("SELECT last_sequence FROM collab_mailbox_cursors WHERE run_id=? AND member_id=?",
                                  (run_id, row["member_id"])).fetchone()[0]
            db.execute("INSERT INTO collab_answer_deliveries VALUES (?,?,?,?,?,?,'accepted',NULL)",
                       (answer_id, run_id, row["attempt_id"], row["member_id"], row["work_attempt_id"], sequence))
            self.store.event(db, self.scope, run_id, "user_answer_accepted",
                {"question_id": question_id, "answer_id": answer_id, "actor": "admin", "member_id": row["member_id"], "outcome": answer["outcome"]})
            return {"id": answer_id, "question_id": question_id, "status": "accepted", "member_id": row["member_id"],
                    "work_attempt_id": row["work_attempt_id"], "sequence": sequence, "outcome": answer["outcome"]}
        result = self.store.mutate(self.scope, request_id, "answer_user_question", run_id,
            {"input_hash": FrozenJSON(value).sha256}, authorize=authenticated, operation=operation)
        return self.budgets._raise_error(result)

    def expire_due(self, run_id, request_id):
        run_id = opaque_id(run_id)
        def operation(db):
            self.runs._wall_expired(db, run_id)
            rows = db.execute("SELECT * FROM collab_decisions WHERE run_id=? AND status='pending' AND expires_ms<=?",
                              (run_id, self.runs.clock_ms())).fetchall()
            for row in rows:
                self._close(db, row, "expired"); self._restore_work(db, row)
            return {"expired": [row["id"] for row in rows]}
        return self.store.mutate(self.scope, request_id, "expire_user_questions", run_id, {},
                                 authorize=lambda db: self.runs._run(db, run_id), operation=operation)


class QuestionService:
    """Authenticated administrative reads/answers; no public route registered yet."""
    def __init__(self, store, auth, workspace, *, clock_ms=None, allowed_actions=frozenset({"read", "answer"})):
        self.store, self.auth = store, auth
        self.access = AdminAccess(auth, workspace, allowed_actions)
        self.clock_ms = clock_ms

    def _repository(self, token, action):
        scope = self.access.principal(token, action)
        kwargs = {} if self.clock_ms is None else {"clock_ms": self.clock_ms}
        return QuestionRepository(RunRepository(self.store, scope, **kwargs), self.auth.redactor)

    def list_questions(self, token, run_id, *, after_sequence=0, limit=50):
        repo = self._repository(token, "read")
        run_id = opaque_id(run_id)
        integer(after_sequence, minimum=0, maximum=2**63-1); integer(limit, maximum=200)
        with self.store.transaction(write=False) as db:
            self.access.principal(token, "read"); repo.runs._run(db, run_id)
            rows = db.execute("SELECT q.*,a.id answer_id,a.actor answer_actor,a.body answer_body,a.outcome answer_outcome,a.created_at answered_at "
                              "FROM collab_decisions q LEFT JOIN collab_decision_answers a ON a.decision_id=q.id "
                              "WHERE q.run_id=? AND q.sequence>? ORDER BY q.sequence LIMIT ?",
                              (run_id, after_sequence, limit+1)).fetchall()
            selected = rows[:limit]
            return {"items": [repo._dto(row) for row in selected], "next_cursor": selected[-1]["sequence"] if selected else after_sequence,
                    "has_more": len(rows)>limit}

    def answer(self, token, run_id, request_id, value):
        repo = self._repository(token, "answer")
        return repo.answer(run_id, request_id, value, authorize=lambda db: self.access.principal(token, "answer"))
