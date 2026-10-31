"""Specification-derived state and completion barriers, independent of UI."""
from dataclasses import FrozenInstanceError
from unittest import TestCase
from uuid import uuid4

from pi_deepagent.collaboration.contracts import CollaborationError
from pi_deepagent.collaboration.state import (
    EntityKind, FailureClass, OperationOutcome, ResultCertainty, SettlementFacts,
    TransitionCause, VersionedState,
)


class StateContractTests(TestCase):
    def test_initial_work_validation_failure_and_repair_preserve_failed_attempt(self):
        item = VersionedState(EntityKind.WORK_ITEM, "pending")
        for target in ("ready", "running", "submitted", "verifying", "repair_pending", "ready", "running"):
            prior = item
            item = item.transition(target, expected_revision=item.revision)
            self.assertEqual(item.revision, prior.revision + 1)
        failed = VersionedState(EntityKind.WORK_ATTEMPT, "verifying").transition("failed", expected_revision=0)
        with self.assertRaises(CollaborationError):
            failed.transition("queued", expected_revision=1, cause=TransitionCause.CONTINUE)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(VersionedState(EntityKind.WORK_ATTEMPT, "queued").revision, 0)

    def test_only_team_summary_can_continue_old_attempt_remains_terminal(self):
        facts = SettlementFacts()
        run = VersionedState(EntityKind.TEAM_RUN, "interrupted", revision=12)
        resumed = run.transition("queued", expected_revision=12, cause=TransitionCause.CONTINUE, facts=facts)
        self.assertEqual((run.status, run.revision), ("interrupted", 12))
        self.assertEqual((resumed.status, resumed.revision), ("queued", 13))
        for kind in (EntityKind.TEAM_RUN, EntityKind.RUN_ATTEMPT):
            with self.subTest(kind=kind), self.assertRaises(CollaborationError):
                VersionedState(kind, "completed").transition("queued", expected_revision=0,
                    cause=TransitionCause.CONTINUE, facts=facts)
        with self.assertRaises(CollaborationError):
            run.transition("queued", expected_revision=12, facts=facts)

    def test_attempt_turn_execution_and_integrated_change_terminal_states_are_immutable(self):
        terminal = {
            EntityKind.RUN_ATTEMPT: ("completed", "failed", "stopped", "interrupted"),
            EntityKind.WORK_ATTEMPT: ("succeeded", "failed", "cancelled", "interrupted"),
            EntityKind.MEMBER_TURN: ("completed", "failed", "cancelled", "interrupted"),
            EntityKind.EXECUTION: ("succeeded", "failed", "cancelled"),
            EntityKind.CHANGE_SET: ("integrated", "superseded", "cancelled"),
        }
        for kind, statuses in terminal.items():
            for status in statuses:
                with self.subTest(kind=kind, status=status), self.assertRaises(CollaborationError):
                    VersionedState(kind, status).transition(status, expected_revision=0)

    def test_stop_or_failed_settlement_never_releases_active_or_unknown_resources(self):
        for active, unknown in ((1, 0), (0, 1), (2, 1)):
            facts = SettlementFacts(active_resources=active, unknown_resources=unknown)
            for target in ("stopped", "failed"):
                with self.subTest(target=target, active=active, unknown=unknown), self.assertRaises(CollaborationError) as raised:
                    VersionedState(EntityKind.TEAM_RUN, "stopping").transition(target, expected_revision=0, facts=facts)
                self.assertEqual(raised.exception.code, "resources_not_drained")
        for kind in (EntityKind.TEAM_RUN, EntityKind.RUN_ATTEMPT):
            stopped = VersionedState(kind, "running").transition("stopping", expected_revision=0)
            self.assertEqual(stopped.transition("failed", expected_revision=1, facts=SettlementFacts()).status, "failed")

    def test_queued_stop_is_direct_only_when_no_start_intent_and_no_resources(self):
        queued = VersionedState(EntityKind.TEAM_RUN, "queued")
        self.assertEqual(queued.transition("stopped", expected_revision=0, facts=SettlementFacts()).status, "stopped")
        for facts in (SettlementFacts(start_claimed=True), SettlementFacts(active_resources=1),
                      SettlementFacts(unknown_resources=1)):
            with self.assertRaises(CollaborationError):
                queued.transition("stopped", expected_revision=0, facts=facts)
            self.assertEqual(queued.transition("stopping", expected_revision=0, facts=facts).status, "stopping")

    def test_completed_requires_all_work_gates_messages_and_decisions_settled(self):
        running = VersionedState(EntityKind.TEAM_RUN, "running")
        self.assertEqual(running.transition("completed", expected_revision=0, facts=SettlementFacts()).status, "completed")
        for field in ("required_work_remaining", "missing_criteria", "unintegrated_changes", "nonpassed_required_checks",
                      "pending_messages", "pending_failure_events", "pending_decisions", "pending_turns",
                      "active_resources", "unknown_resources", "pending_operations"):
            with self.subTest(field=field), self.assertRaises(CollaborationError):
                running.transition("completed", expected_revision=0, facts=SettlementFacts(**{field: 1}))
        with self.assertRaises(CollaborationError):
            running.transition("completed", expected_revision=0)

    def test_stale_revision_unknown_status_and_bad_facts_are_rejected(self):
        state = VersionedState(EntityKind.WORK_ITEM, "ready", revision=7)
        with self.assertRaises(CollaborationError) as raised:
            state.transition("running", expected_revision=6)
        self.assertEqual((raised.exception.status, raised.exception.code), (409, "stale_revision"))
        for call in (lambda: VersionedState("invalid kind", "running"),
                     lambda: VersionedState(EntityKind.WORK_ITEM, "made-up-state"),
                     lambda: VersionedState(EntityKind.WORK_ITEM, "ready", revision=True),
                     lambda: state.transition("running", expected_revision=True),
                     lambda: SettlementFacts(pending_messages=-1),
                     lambda: SettlementFacts(start_claimed=1)):
            with self.assertRaises(CollaborationError): call()
        with self.assertRaises(FrozenInstanceError): state.status = "completed"

    def test_execution_unknown_requires_explicit_reconciliation_identity(self):
        unknown = VersionedState(EntityKind.EXECUTION, "unknown")
        for kwargs in ({}, {"cause": TransitionCause.RECONCILE}):
            with self.assertRaises(CollaborationError) as raised:
                unknown.transition("succeeded", expected_revision=0, **kwargs)
            self.assertEqual(raised.exception.code, "reconciliation_required")
        resolved = unknown.transition("failed", expected_revision=0, cause=TransitionCause.RECONCILE,
                                      reconciliation_id=str(uuid4()))
        self.assertEqual(resolved.status, "failed")
        # This is a reference contract; storage/service must verify the referenced journal.
        self.assertEqual(unknown.status, "unknown")

    def test_waits_remain_work_states_and_do_not_invent_attempt_statuses(self):
        for wait in ("waiting_user", "waiting_peer", "waiting_approval"):
            state = VersionedState(EntityKind.WORK_ITEM, "running").transition(wait, expected_revision=0)
            self.assertEqual(state.transition("ready", expected_revision=1).status, "ready")
            with self.assertRaises(CollaborationError): VersionedState(EntityKind.MEMBER_TURN, wait)
            with self.assertRaises(CollaborationError): VersionedState(EntityKind.WORK_ATTEMPT, wait)
        draft = VersionedState(EntityKind.TEAM, "draft")
        for facts in (None, SettlementFacts(active_runs=1), SettlementFacts(pending_operations=1)):
            with self.assertRaises(CollaborationError):
                draft.transition("archived", expected_revision=0, facts=facts)
        archived = draft.transition("archived", expected_revision=0, facts=SettlementFacts())
        with self.assertRaises(CollaborationError): archived.transition("draft", expected_revision=1)
        self.assertEqual(archived.transition("draft", expected_revision=1, cause=TransitionCause.RESTORE).status, "draft")

    def test_known_unclassified_failure_is_distinct_from_unknown_result(self):
        known = OperationOutcome(ResultCertainty.KNOWN, FailureClass.UNCLASSIFIED_FAILURE, exit_code=17)
        unknown = OperationOutcome(ResultCertainty.UNKNOWN)
        self.assertTrue(known.can_diagnose_failure)
        self.assertFalse(unknown.can_diagnose_failure)
        self.assertFalse(OperationOutcome(ResultCertainty.KNOWN, exit_code=0).can_diagnose_failure)
        for call in (lambda: OperationOutcome("maybe"),
                     lambda: OperationOutcome(ResultCertainty.KNOWN, "invalid class"),
                     lambda: OperationOutcome(ResultCertainty.KNOWN, exit_code=True)):
            with self.assertRaises(CollaborationError): call()
