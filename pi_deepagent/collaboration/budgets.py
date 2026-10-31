"""Transactional cumulative budgets for service-bound run actors.

No budget result authorizes a command, installation, or candidate publication.
Settlement is a trusted coordinator operation; unknown outcomes remain reserved
and need the later journal reconciler, not an arbitrary second request ID.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from uuid import uuid4

from .contracts import CollaborationError, FrozenJSON, integer, opaque_id
from .runs import RunBinding, RunRepository, conflict


class BudgetRepository:
    def __init__(self, runs: RunRepository):
        self.runs, self.store, self.scope = runs, runs.store, runs.scope

    @staticmethod
    def _raise_error(result):
        if "error" in result:
            error = result["error"]
            raise CollaborationError(error["status"], error["code"], error["message"])
        return result

    @staticmethod
    def _error(status, code, message):
        return {"error": {"status": status, "code": code, "message": message}}

    @staticmethod
    def pending_for_actor(db, binding, *, include_reserved=False):
        """Already-authorized callers compare identity independently of grants."""
        rows = db.execute("SELECT * FROM collab_budget_reservations WHERE run_id=? "
                          "AND (status='unknown' OR (status='reserved' AND ?))",
                          (binding.run_id, include_reserved)).fetchall()
        identity = ("run_id", "attempt_id", "actor", "member_id", "candidate_id", "plan_hash")
        actor = asdict(binding)
        return [row for row in rows if all(json.loads(row["binding_json"])[key] == actor[key] for key in identity)]

    def reserve_in_transaction(self, db, binding, request_id, metric, amount):
        """For mailbox acceptance: call inside the same state/ledger transaction."""
        run = self.runs.authorize_binding(db, binding)
        if metric not in ("model_calls", "messages"):
            raise CollaborationError(400, "invalid_metric", "Unrecognized run budget")
        integer(amount)
        if run["status"] != "running":
            return self._error(409, "dispatch_closed", "Run is not accepting new operations")
        if self.runs._wall_expired(db, run["id"]):
            return self._error(429, "budget_exhausted", "Run wall-time budget is exhausted")
        actor = FrozenJSON(asdict(binding)).json
        if self.pending_for_actor(db, binding):
            return self._error(409, "actor_operation_unknown", "Actor has an unresolved operation; reconcile before another dispatch")
        totals = db.execute("SELECT * FROM collab_budget_totals WHERE run_id=? AND metric=?", (run["id"], metric)).fetchone()
        limit = json.loads(run["policy_json"])["limits"][metric]
        if totals["used"] + totals["reserved"] + amount > limit:
            self.runs._close_dispatch(db, run["id"], metric)
            return self._error(429, "budget_exhausted", "Run budget cannot reserve another operation")
        identifier = str(uuid4())
        db.execute("INSERT INTO collab_budget_reservations VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (identifier, run["id"], binding.attempt_id, request_id, metric, amount, binding.actor,
                    binding.member_id, actor, "reserved"))
        db.execute("UPDATE collab_budget_totals SET reserved=reserved+? WHERE run_id=? AND metric=?",
                   (amount, run["id"], metric))
        self.store.event(db, self.scope, run["id"], "budget_reserved",
                         {"reservation_id": identifier, "metric": metric, "amount": amount, "actor": binding.actor})
        return {"id": identifier, "status": "reserved", "metric": metric, "amount": amount}

    def reserve(self, binding, request_id, metric, *, amount=1):
        if not isinstance(binding, RunBinding):
            raise CollaborationError(403, "invalid_binding", "A service-bound actor is required")
        request_id = opaque_id(request_id)
        integer(amount)
        result = self.store.mutate(self.scope, request_id, "reserve_budget", binding.run_id,
            {"binding": asdict(binding), "metric": metric, "amount": amount},
            authorize=lambda db: self.runs.authorize_binding(db, binding),
            operation=lambda db: self.reserve_in_transaction(db, binding, request_id, metric, amount))
        # Exhaustion must COMMIT its stop intent and ledger before reporting 429.
        return self._raise_error(result)

    def settle_in_transaction(self, db, binding, reservation_id, *, certainty, started):
        # A new policy cannot strand the known result of an already reserved
        # operation. This remains coordinator-only, never a model tool input.
        run = self.runs.authorize_binding(db, binding, allow_historical_policy=True)
        self.runs._wall_expired(db, run["id"])
        row = db.execute("SELECT * FROM collab_budget_reservations WHERE id=?", (opaque_id(reservation_id),)).fetchone()
        if row is None:
            raise CollaborationError(404, "reservation_not_found", "Reservation was not found")
        if row["run_id"] != binding.run_id or row["binding_json"] != FrozenJSON(asdict(binding)).json:
            raise CollaborationError(403, "reservation_mismatch", "Reservation belongs to another actor")
        if certainty not in ("known", "unknown") or type(started) is not bool:
            raise CollaborationError(400, "invalid_outcome", "Trusted operation outcome is required")
        if row["status"] == "unknown":
            conflict("reconciliation_required", "Unknown reservation requires journal reconciliation")
        if row["status"] != "reserved":
            conflict("reservation_settled", "Reservation already has an immutable result")
        status = "unknown" if certainty == "unknown" else ("consumed" if started else "released")
        db.execute("UPDATE collab_budget_reservations SET status=? WHERE id=?", (status, reservation_id))
        if certainty == "known":
            db.execute("UPDATE collab_budget_totals SET reserved=reserved-?,used=used+? WHERE run_id=? AND metric=?",
                       (row["amount"], row["amount"] if started else 0, row["run_id"], row["metric"]))
        self.store.event(db, self.scope, run["id"], "budget_settled",
                         {"reservation_id": reservation_id, "status": status})
        return {"id": reservation_id, "status": status, "metric": row["metric"], "amount": row["amount"]}

    def settle(self, binding, reservation_id, request_id, *, certainty, started):
        if not isinstance(binding, RunBinding):
            raise CollaborationError(403, "invalid_binding", "A service-bound actor is required")
        return self.store.mutate(self.scope, request_id, "settle_budget", binding.run_id,
            {"binding": asdict(binding), "reservation_id": reservation_id, "certainty": certainty, "started": started},
            authorize=lambda db: self.runs.authorize_binding(db, binding, allow_historical_policy=True),
            operation=lambda db: self.settle_in_transaction(db, binding, reservation_id, certainty=certainty, started=started))
