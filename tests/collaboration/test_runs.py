"""Run/attempt/grant/budget persistence; fixtures never start actual execution."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from uuid import uuid4

from pi_deepagent.collaboration.budgets import BudgetRepository
from pi_deepagent.collaboration.contracts import CollaborationError
from pi_deepagent.collaboration.runs import RunRepository
from pi_deepagent.collaboration.service import TeamService
from pi_deepagent.collaboration.storage import CollaborationStore, Scope
from pi_deepagent.web.auth import AuthService, Redactor


class RunFixture(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.workspace = root / "workspace"; self.workspace.mkdir()
        self.data = root / "data"; self.data.mkdir()
        self.path = self.data / "tasks.sqlite"
        CollaborationStore.initialize_empty(self.path)
        self.store = CollaborationStore(self.path); self.addCleanup(self.store.close)
        self.auth = AuthService(self.data, Redactor())
        self.auth.initialize("temporary-run-fixture-admin-123")
        self.token, _ = self.auth.login("admin", "temporary-run-fixture-admin-123", "test")
        self.service = TeamService(self.store, self.auth, self.workspace)
        self.config = {"name": "fixture", "goal": "domain-only testing", "criteria": ["records are correct"],
            "members": [{"name": "lead", "responsibility": "coordinate", "goal": "ship", "orchestrator": True},
                        {"name": "builder", "responsibility": "implement", "goal": "repair", "orchestrator": False},
                        {"name": "verifier", "responsibility": "test", "goal": "validate", "orchestrator": False}],
            "policy": {"limits": {"model_calls": 2, "messages": 3, "wall_seconds": 10}}}
        self.team = self.service.create_team(self.token, str(uuid4()), self.config)
        self.scope = Scope("admin", str(self.workspace.resolve()))
        self.now = 100_000
        self.runs = RunRepository(self.store, self.scope, clock_ms=lambda: self.now)
        self.prepare_request = str(uuid4())
        self.run = self.runs.prepare(self.team["id"], self.prepare_request, expected_revision=0)
        self.budgets = BudgetRepository(self.runs)

    def activate(self):
        self.run = self.runs.claim_start(self.run["id"], str(uuid4()), expected_revision=self.run["revision"])
        return self.runs.bind_member(self.run["id"], self.run["members"][0]["id"])

    def finish(self):
        run = self.runs.get(self.run["id"])
        self.runs.stop(run["id"], str(uuid4()), expected_revision=run["revision"])
        return self.runs.finish_stopping(run["id"], str(uuid4()), attempt_id=run["attempt"]["id"])

    def extend_limits(self, new):
        from pi_deepagent.collaboration.policies import PolicyService
        service = PolicyService(self.store, self.auth, self.workspace, clock_ms=lambda: self.now)
        run = self.runs.get(self.run["id"])
        request = service.create_request(self.token, run["id"], str(uuid4()), {"kind":"budget_extension",
            "old":{key:run["policy"]["limits"][key] for key in new}, "new":new, "reason":"Explicit fixture admin extension",
            "policy_version":run["policy_version"], "expected_revision":run["revision"]})
        return service.decide(self.token, run["id"], str(uuid4()), {"approval_id":request["id"],"decision":"approve",
            "reason":"Approved for fixture evidence", "expected_revision":request["revision"]})


class RunLifecycleTests(RunFixture):
    def test_scoped_run_and_member_lookup_fail_closed(self):
        with self.assertRaises(CollaborationError) as raised: self.runs.get(str(uuid4()))
        self.assertEqual(raised.exception.code, "run_not_found")
        with self.assertRaises(CollaborationError) as raised: self.runs.bind_member(self.run["id"], str(uuid4()))
        self.assertEqual(raised.exception.code, "member_mismatch")
        foreign = RunRepository(self.store, Scope("other", self.scope.workspace))
        with self.assertRaises(CollaborationError) as raised: foreign.get(self.run["id"])
        self.assertEqual(raised.exception.status, 403)
        with self.assertRaises(CollaborationError) as raised: self.runs.prepare(str(uuid4()), str(uuid4()), expected_revision=0)
        self.assertEqual(raised.exception.code, "team_not_found")

    def test_prepare_checks_legacy_activity_and_archived_team_before_allocating(self):
        self.activate()
        self.finish()
        with self.store.transaction() as db:
            db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY,status TEXT)")
            db.execute("INSERT INTO tasks VALUES ('legacy-fixture','running')")
        with self.assertRaises(CollaborationError) as raised: self.runs.prepare(self.team["id"], str(uuid4()), expected_revision=0)
        self.assertEqual(raised.exception.code, "top_level_busy")
        with self.store.transaction() as db:
            db.execute("UPDATE tasks SET status='completed'")
            db.execute("UPDATE collab_teams SET status='archived'")
        with self.assertRaises(CollaborationError) as raised: self.runs.prepare(self.team["id"], str(uuid4()), expected_revision=0)
        self.assertEqual(raised.exception.code, "team_archived")
        with self.assertRaises(CollaborationError) as raised:
            stopped = self.runs.get(self.run["id"])
            self.runs.continue_run(stopped["id"], str(uuid4()), expected_revision=stopped["revision"])
        self.assertEqual(raised.exception.code, "team_archived")
        with self.store.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_activity").fetchone()[0], 0)

    def test_run_snapshot_and_member_versions_do_not_follow_draft_edits(self):
        before = self.runs.get(self.run["id"])
        self.config["name"] = "edited draft"
        self.config["members"][0]["goal"] = "new draft goal"
        self.service.edit_team(self.token, self.team["id"], str(uuid4()), self.config, expected_revision=0)
        self.assertEqual(self.runs.get(self.run["id"]), before)
        self.assertTrue(all(m["definition_version"] == 1 for m in before["members"]))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction() as db: db.execute("UPDATE collab_members SET spec_json='{}'")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction() as db: db.execute("UPDATE collab_runs SET config_json='{}'")

    def test_top_level_reservation_is_persistent_and_duplicate_prepare_returns_original(self):
        self.assertEqual(self.runs.prepare(self.team["id"], self.prepare_request, expected_revision=0), self.run)
        with self.assertRaises(CollaborationError) as raised:
            self.runs.prepare(self.team["id"], str(uuid4()), expected_revision=0)
        self.assertEqual(raised.exception.code, "top_level_busy")
        self.store.close()
        with CollaborationStore(self.path) as store:
            runs = RunRepository(store, self.scope, clock_ms=lambda: self.now)
            self.assertEqual(runs.get(self.run["id"]), self.run)
            with self.assertRaises(CollaborationError): runs.prepare(self.team["id"], str(uuid4()), expected_revision=0)

    def test_start_and_queued_stop_race_have_only_one_valid_result(self):
        barrier = Barrier(2)
        def race(action):
            with CollaborationStore(self.path) as store:
                runs = RunRepository(store, self.scope, clock_ms=lambda: self.now)
                barrier.wait(timeout=5)
                try:
                    method = runs.claim_start if action == "start" else runs.stop
                    return method(self.run["id"], str(uuid4()), expected_revision=0)["status"]
                except CollaborationError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(race, ("start", "stop")))
        self.assertEqual(results.count("stale_revision"), 1)
        self.assertIn(self.runs.get(self.run["id"])["status"], ("running", "stopped"))
        if "stopped" in results:
            with self.store.transaction(write=False) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_activity").fetchone()[0], 0)

    def test_continuation_preserves_old_attempt_usage_and_excludes_terminal_gap(self):
        actor = self.activate()
        reservation = self.budgets.reserve(actor, str(uuid4()), "model_calls")
        self.budgets.settle(actor, reservation["id"], str(uuid4()), certainty="known", started=True)
        self.now += 1000
        stopped = self.finish()
        old_attempt = dict(stopped["attempt"])
        self.now += 3_600_000
        resumed = self.runs.continue_run(stopped["id"], str(uuid4()), expected_revision=stopped["revision"])
        self.assertNotEqual(resumed["attempt"]["id"], old_attempt["id"])
        self.assertEqual(resumed["attempt"]["number"], 2)
        self.assertEqual(resumed["wall_used_ms"], 1000)
        self.assertEqual(resumed["budgets"]["model_calls"], {"used": 1, "reserved": 0})
        with self.store.transaction(write=False) as db:
            self.assertEqual(dict(db.execute("SELECT * FROM collab_run_attempts WHERE id=?", (old_attempt["id"],)).fetchone()), old_attempt)
        with self.assertRaises(CollaborationError) as raised:
            self.budgets.reserve(actor, str(uuid4()), "model_calls")
        self.assertEqual(raised.exception.code, "stale_attempt")
        self.run = resumed; self.activate(); self.now += 3000
        final = self.finish()
        self.assertEqual(final["wall_used_ms"], 4000)

    def test_late_drain_or_interrupt_from_old_attempt_cannot_finish_new_attempt(self):
        self.activate(); stopped = self.finish()
        old = stopped["attempt"]["id"]
        self.run = self.runs.continue_run(stopped["id"], str(uuid4()), expected_revision=stopped["revision"])
        self.activate()
        current = self.runs.get(self.run["id"])
        self.runs.stop(current["id"], str(uuid4()), expected_revision=current["revision"])
        for method in (self.runs.finish_stopping, self.runs.mark_interrupted):
            with self.assertRaises(CollaborationError) as raised: method(current["id"], str(uuid4()), attempt_id=old)
            self.assertEqual(raised.exception.code, "stale_attempt")
        self.assertEqual(self.runs.get(current["id"])["status"], "stopping")

    def test_interruption_keeps_fence_and_reservations_unknown_without_automatic_restart(self):
        actor = self.activate()
        self.budgets.reserve(actor, str(uuid4()), "model_calls")
        self.now += 500
        interrupted = self.runs.mark_interrupted(self.run["id"], str(uuid4()), attempt_id=actor.attempt_id)
        self.assertEqual(interrupted["attempt"]["status"], "interrupted")
        self.assertEqual(interrupted["wall_used_ms"], 500)
        with self.store.transaction(write=False) as db:
            self.assertEqual(db.execute("SELECT status FROM collab_budget_reservations").fetchone()[0], "unknown")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM collab_activity").fetchone()[0], 1)
        with self.assertRaises(CollaborationError):
            self.runs.continue_run(self.run["id"], str(uuid4()), expected_revision=interrupted["revision"])


class RunBudgetTests(RunFixture):
    def test_invalid_budget_inputs_wrong_reservation_and_closed_dispatch_preserve_totals(self):
        actor = self.activate()
        with self.assertRaises(CollaborationError) as raised: self.budgets.reserve(None, str(uuid4()), "model_calls")
        self.assertEqual(raised.exception.code, "invalid_binding")
        with self.assertRaises(CollaborationError) as raised: self.budgets.reserve(actor, str(uuid4()), "unknown_metric")
        self.assertEqual(raised.exception.code, "invalid_metric")
        reservation = self.budgets.reserve(actor, str(uuid4()), "model_calls")
        for binding, identifier, certainty, started, code in (
            (actor, str(uuid4()), "known", True, "reservation_not_found"),
            (actor, reservation["id"], "model_claimed_success", True, "invalid_outcome"),
            (actor, reservation["id"], "known", 1, "invalid_outcome"),
            (None, reservation["id"], "known", True, "invalid_binding"),
            (self.runs.bind_member(actor.run_id, self.run["members"][1]["id"]), reservation["id"], "known", True, "reservation_mismatch")):
            with self.subTest(code=code), self.assertRaises(CollaborationError) as raised:
                self.budgets.settle(binding, identifier, str(uuid4()), certainty=certainty, started=started)
            self.assertEqual(raised.exception.code, code)
        run = self.runs.get(actor.run_id)
        self.runs.stop(run["id"], str(uuid4()), expected_revision=run["revision"])
        with self.assertRaises(CollaborationError) as raised: self.budgets.reserve(actor, str(uuid4()), "messages")
        self.assertEqual(raised.exception.code, "dispatch_closed")
        self.budgets.settle(actor, reservation["id"], str(uuid4()), certainty="known", started=False)
        self.assertEqual(self.runs.get(actor.run_id)["budgets"]["model_calls"], {"used": 0, "reserved": 0})

    def test_grant_rejects_tampered_valid_peer_policy_and_revoked_replay(self):
        actor = self.activate()
        request = str(uuid4())
        reservation = self.budgets.reserve(actor, request, "model_calls")
        peer = self.run["members"][1]["id"]
        for changed in (replace(actor, member_id=peer), replace(actor, policy_version=2), replace(actor, grant_id=str(uuid4()))):
            with self.assertRaises(CollaborationError) as raised: self.budgets.reserve(changed, str(uuid4()), "model_calls")
            self.assertEqual(raised.exception.status, 403)
        with self.store.transaction() as db:
            db.execute("UPDATE collab_actor_grants SET active=0 WHERE id=?", (actor.grant_id,))
        with self.assertRaises(CollaborationError) as raised: self.budgets.reserve(actor, request, "model_calls")
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(self.runs.get(self.run["id"])["budgets"]["model_calls"], {"used": 0, "reserved": 1})

    def test_reserve_deduplication_consumption_and_release_are_atomic(self):
        actor = self.activate(); request = str(uuid4())
        reservation = self.budgets.reserve(actor, request, "model_calls")
        self.assertEqual(self.budgets.reserve(actor, request, "model_calls"), reservation)
        consumed = self.budgets.settle(actor, reservation["id"], str(uuid4()), certainty="known", started=True)
        self.assertEqual(consumed["status"], "consumed")
        with self.assertRaises(CollaborationError):
            self.budgets.settle(actor, reservation["id"], str(uuid4()), certainty="known", started=False)
        second = self.budgets.reserve(actor, str(uuid4()), "model_calls")
        self.budgets.settle(actor, second["id"], str(uuid4()), certainty="known", started=False)
        self.assertEqual(self.runs.get(self.run["id"])["budgets"]["model_calls"], {"used": 1, "reserved": 0})

    def test_parallel_last_budget_reservation_never_overcommits_and_stop_intent_commits(self):
        actor = self.activate()
        first = self.budgets.reserve(actor, str(uuid4()), "model_calls")
        self.budgets.settle(actor, first["id"], str(uuid4()), certainty="known", started=True)
        actors = [self.runs.bind_member(self.run["id"], member["id"]) for member in self.run["members"][1:]]
        barrier = Barrier(2)
        def reserve(binding):
            with CollaborationStore(self.path) as store:
                budgets = BudgetRepository(RunRepository(store, self.scope, clock_ms=lambda: self.now))
                barrier.wait(timeout=5)
                try: return budgets.reserve(binding, str(uuid4()), "model_calls")["status"]
                except CollaborationError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(reserve, actors))
        self.assertEqual(sorted(results), ["budget_exhausted", "reserved"])
        run = self.runs.get(self.run["id"])
        self.assertEqual(run["budgets"]["model_calls"], {"used": 1, "reserved": 1})
        self.assertEqual((run["status"], run["terminal_intent"]), ("stopping", "failed"))

    def test_unknown_is_not_refunded_or_replayed_by_changing_request_or_grant(self):
        actor = self.activate()
        reservation = self.budgets.reserve(actor, str(uuid4()), "model_calls")
        self.budgets.settle(actor, reservation["id"], str(uuid4()), certainty="unknown", started=True)
        newer = self.runs.bind_member(actor.run_id, actor.member_id)
        with self.assertRaises(CollaborationError) as raised: self.budgets.reserve(newer, str(uuid4()), "model_calls")
        self.assertEqual(raised.exception.code, "actor_operation_unknown")
        with self.assertRaises(CollaborationError) as raised:
            self.budgets.settle(actor, reservation["id"], str(uuid4()), certainty="known", started=False)
        self.assertEqual(raised.exception.code, "reconciliation_required")
        peer = self.runs.bind_member(actor.run_id, self.run["members"][1]["id"])
        self.assertEqual(self.budgets.reserve(peer, str(uuid4()), "model_calls")["status"], "reserved")
        run = self.runs.get(actor.run_id)
        self.runs.stop(run["id"], str(uuid4()), expected_revision=run["revision"])
        with self.assertRaises(CollaborationError):
            self.runs.finish_stopping(run["id"], str(uuid4()), attempt_id=actor.attempt_id)
        self.assertEqual(self.runs.get(run["id"])["budgets"]["model_calls"], {"used": 0, "reserved": 2})

    def test_wall_exhaustion_closes_dispatch_before_reporting_error_and_counts_wait_time(self):
        actor = self.activate(); self.now += 10_000
        request = str(uuid4())
        for repeat in range(2):
            with self.assertRaises(CollaborationError) as raised: self.budgets.reserve(actor, request, "model_calls")
            self.assertEqual((raised.exception.status, raised.exception.code), (429, "budget_exhausted"))
        run = self.runs.get(actor.run_id)
        self.assertEqual((run["status"], run["wall_used_ms"]), ("stopping", 10_000))
        failed = self.runs.finish_stopping(run["id"], str(uuid4()), attempt_id=actor.attempt_id)
        self.assertEqual(failed["status"], "failed")
        with self.assertRaises(CollaborationError) as raised:
            self.runs.continue_run(run["id"], str(uuid4()), expected_revision=failed["revision"])
        self.assertEqual(raised.exception.code, "budget_extension_required")

    def test_policy_staleness_and_database_guards_cannot_reduce_consumed_usage(self):
        actor = self.activate()
        reservation = self.budgets.reserve(actor, str(uuid4()), "model_calls")
        self.budgets.settle(actor, reservation["id"], str(uuid4()), certainty="known", started=True)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction() as db: db.execute("UPDATE collab_budget_totals SET used=0 WHERE metric='model_calls'")
        self.extend_limits({"wall_seconds": 11})
        with self.assertRaises(CollaborationError) as raised: self.budgets.reserve(actor, str(uuid4()), "model_calls")
        self.assertEqual(raised.exception.code, "stale_policy")

    def test_wall_clock_rollback_does_not_reduce_charged_time_or_make_negative_attempt(self):
        actor = self.activate(); self.now += 1000
        reserved = self.budgets.reserve(actor, str(uuid4()), "model_calls")
        self.now -= 5000
        self.budgets.settle(actor, reserved["id"], str(uuid4()), certainty="known", started=True)
        stopped = self.finish()
        self.assertEqual(stopped["wall_used_ms"], 1000)
        self.assertGreaterEqual(stopped["attempt"]["ended_ms"], stopped["attempt"]["started_ms"])
