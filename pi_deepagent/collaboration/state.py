"""Lifecycle topology and service-evaluated settlement contracts from spec 12.

These immutable values are not authorization or a database CAS. The service
must derive facts and verify reconciliation journal IDs inside its transaction;
no model/public request is allowed to supply settlement facts or final states.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from types import MappingProxyType

from .contracts import CollaborationError, integer, invalid, opaque_id


class EntityKind(StrEnum):
    TEAM = "team"
    TEAM_RUN = "team_run"
    RUN_ATTEMPT = "run_attempt"
    WORK_ITEM = "work_item"
    WORK_ATTEMPT = "work_attempt"
    MEMBER_TURN = "member_turn"
    EXECUTION = "execution"
    CHANGE_SET = "change_set"


class TransitionCause(StrEnum):
    NORMAL = "normal"
    CONTINUE = "continue"
    RESTART = "restart"
    RECONCILE = "reconcile"
    RESTORE = "restore"


class ResultCertainty(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"


class FailureClass(StrEnum):
    CODE_FAILURE = "code_failure"
    ENVIRONMENT_FAILURE = "environment_failure"
    POLICY_BLOCKED = "policy_blocked"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNCLASSIFIED_FAILURE = "unclassified_failure"


def _enum(kind, value):
    try:
        return kind(value)
    except (TypeError, ValueError):
        invalid("invalid_state", "Unrecognized collaboration state or result category")


@dataclass(frozen=True, slots=True)
class OperationOutcome:
    certainty: ResultCertainty
    failure_class: FailureClass | None = None
    exit_code: int | None = None

    def __post_init__(self):
        object.__setattr__(self, "certainty", _enum(ResultCertainty, self.certainty))
        if self.failure_class is not None:
            object.__setattr__(self, "failure_class", _enum(FailureClass, self.failure_class))
        if self.exit_code is not None and type(self.exit_code) is not int:
            invalid("invalid_exit_code", "Exit code must be an integer or unknown")

    @property
    def can_diagnose_failure(self):
        return self.certainty == ResultCertainty.KNOWN and self.failure_class not in (None, FailureClass.CANCELLED)


@dataclass(frozen=True, slots=True)
class SettlementFacts:
    required_work_remaining: int = 0
    missing_criteria: int = 0
    unintegrated_changes: int = 0
    nonpassed_required_checks: int = 0
    pending_messages: int = 0
    pending_failure_events: int = 0
    pending_decisions: int = 0
    pending_turns: int = 0
    active_resources: int = 0
    unknown_resources: int = 0
    pending_operations: int = 0
    active_runs: int = 0
    start_claimed: bool = False

    def __post_init__(self):
        for key, value in asdict(self).items():
            if key != "start_claimed":
                integer(value, minimum=0)
        if type(self.start_claimed) is not bool:
            invalid("invalid_facts", "Start intent must be an explicit boolean")

    @property
    def drained(self):
        return self.active_resources == self.unknown_resources == self.pending_operations == 0

    @property
    def complete(self):
        return self.drained and not any(value for key, value in asdict(self).items()
                                       if key not in {"start_claimed", "active_runs"})


_RUN = {
    "queued": {"running", "stopping", "stopped", "interrupted"},
    "running": {"completed", "failed", "stopping", "interrupted"},
    "stopping": {"stopped", "failed", "interrupted"},
    "completed": set(), "failed": set(), "stopped": set(), "interrupted": set(),
}
_WAIT = {"ready", "verifying", "repair_pending", "failed", "cancelled", "interrupted"}
_TRANSITIONS = MappingProxyType({
    EntityKind.TEAM: {"draft": {"archived"}, "archived": {"draft"}},
    EntityKind.TEAM_RUN: {**_RUN, "failed": {"queued"}, "stopped": {"queued"}, "interrupted": {"queued"}},
    EntityKind.RUN_ATTEMPT: _RUN,
    EntityKind.WORK_ITEM: {
        "pending": {"ready", "waiting_user", "waiting_approval", "failed", "cancelled", "interrupted"},
        "ready": {"pending", "running", "waiting_peer", "waiting_user", "waiting_approval", "failed", "cancelled", "interrupted"},
        "running": {"submitted", "waiting_peer", "waiting_user", "waiting_approval", "failed", "cancelled", "interrupted"},
        "waiting_peer": _WAIT, "waiting_user": _WAIT, "waiting_approval": _WAIT,
        "submitted": {"verifying", "waiting_approval", "failed", "cancelled", "interrupted"},
        "verifying": {"completed", "repair_pending", "waiting_user", "waiting_approval", "failed", "cancelled", "interrupted"},
        "repair_pending": {"ready", "waiting_user", "waiting_approval", "failed", "cancelled", "interrupted"},
        "completed": set(), "failed": {"pending"}, "cancelled": {"pending"}, "interrupted": {"pending"},
    },
    EntityKind.WORK_ATTEMPT: {
        "queued": {"running", "failed", "cancelled", "interrupted"},
        "running": {"submitted", "failed", "cancelled", "interrupted"},
        "submitted": {"verifying", "failed", "cancelled", "interrupted"},
        "verifying": {"succeeded", "failed", "cancelled", "interrupted"},
        "succeeded": set(), "failed": set(), "cancelled": set(), "interrupted": set(),
    },
    EntityKind.MEMBER_TURN: {
        "queued": {"running", "failed", "cancelled", "interrupted"},
        "running": {"completed", "failed", "cancelled", "interrupted"},
        "completed": set(), "failed": set(), "cancelled": set(), "interrupted": set(),
    },
    EntityKind.EXECUTION: {
        "queued": {"starting", "cancelled"},
        "starting": {"running", "failed", "stopping", "unknown"},
        "running": {"succeeded", "failed", "stopping", "unknown"},
        "stopping": {"succeeded", "failed", "cancelled", "unknown"},
        "unknown": {"running", "stopping", "succeeded", "failed", "cancelled"},
        "succeeded": set(), "failed": set(), "cancelled": set(),
    },
    EntityKind.CHANGE_SET: {
        "submitted": {"scope_blocked", "conflict", "validating", "superseded", "cancelled"},
        "scope_blocked": {"submitted", "superseded", "cancelled"},
        "conflict": {"validating", "superseded", "cancelled"},
        "validating": {"validation_failed", "ready", "conflict", "superseded", "cancelled"},
        "validation_failed": {"validating", "superseded", "cancelled"},
        "ready": {"integrated", "validating", "superseded", "cancelled"},
        "integrated": set(), "superseded": set(), "cancelled": set(),
    },
})
# Freeze nested maps/sets as well: no scheduler or model-specific code edits topology.
TRANSITIONS = MappingProxyType({kind: MappingProxyType({status: frozenset(targets) for status, targets in states.items()})
                                for kind, states in _TRANSITIONS.items()})


@dataclass(frozen=True, slots=True)
class VersionedState:
    kind: EntityKind
    status: str
    revision: int = 0

    def __post_init__(self):
        object.__setattr__(self, "kind", _enum(EntityKind, self.kind))
        integer(self.revision, minimum=0)
        if not isinstance(self.status, str) or self.status not in TRANSITIONS[self.kind]:
            invalid("invalid_state", "State is not defined for this lifecycle")

    def transition(self, target: str, *, expected_revision: int, cause=TransitionCause.NORMAL,
                   facts: SettlementFacts | None = None, reconciliation_id: str | None = None):
        integer(expected_revision, minimum=0)
        cause = _enum(TransitionCause, cause)
        if expected_revision != self.revision:
            raise CollaborationError(409, "stale_revision", "Resource revision has changed")
        if not isinstance(target, str) or target not in TRANSITIONS[self.kind][self.status]:
            raise CollaborationError(409, "invalid_transition", "Lifecycle transition is not allowed")
        if facts is not None and not isinstance(facts, SettlementFacts):
            invalid("invalid_facts", "Service-derived settlement facts are required")
        continuing = ((self.kind == EntityKind.TEAM_RUN and target == "queued") or
                      (self.kind == EntityKind.WORK_ITEM and self.status in {"failed", "cancelled", "interrupted"}))
        if continuing and cause != TransitionCause.CONTINUE:
            raise CollaborationError(409, "continue_required", "An explicit continuation is required")
        if self.kind == EntityKind.TEAM and target == "draft" and cause != TransitionCause.RESTORE:
            raise CollaborationError(409, "restore_required", "An explicit restore is required")
        if self.kind == EntityKind.TEAM and target == "archived":
            if facts is None or not facts.drained or facts.active_runs:
                raise CollaborationError(409, "team_active", "Team has active or unresolved resources")
        if self.kind in {EntityKind.TEAM_RUN, EntityKind.RUN_ATTEMPT}:
            if target in {"completed", "failed", "stopped"} or continuing:
                if facts is None or not facts.drained:
                    raise CollaborationError(409, "resources_not_drained", "Execution resources are not confirmed drained")
            if target == "completed" and not facts.complete:
                raise CollaborationError(409, "settlement_pending", "Required work, evidence or accepted messages remain")
            if self.status == "queued" and target == "stopped" and facts.start_claimed:
                raise CollaborationError(409, "start_claimed", "Start intent must first be reconciled through stopping")
        if self.kind == EntityKind.EXECUTION and self.status == "unknown":
            if cause != TransitionCause.RECONCILE or reconciliation_id is None:
                raise CollaborationError(409, "reconciliation_required", "Unknown execution must first be reconciled")
            opaque_id(reconciliation_id)
        return replace(self, status=target, revision=self.revision + 1)
