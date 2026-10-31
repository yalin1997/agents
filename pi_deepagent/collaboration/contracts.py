"""Framework-neutral immutable inputs and server-owned collaboration identities.

These contracts validate data; they do not grant permissions, start execution,
or apply a database migration. Runtime authorization belongs to the service.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import hashlib
import json
import math
import re
from typing import Any
from uuid import UUID, uuid4


class CollaborationError(Exception):
    """Safe transport error: code/message never contain untrusted input values."""
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def invalid(code="invalid_value", message="Invalid collaboration input"):
    raise CollaborationError(400, code, message)


def strict_fields(value, required=(), optional=()):
    if not isinstance(value, dict) or not set(required).issubset(value) or set(value) - set(required) - set(optional):
        invalid("invalid_fields", "Request fields do not match the contract")
    return value


def required_text(value):
    if not isinstance(value, str) or not value.strip():
        invalid("invalid_text", "A non-empty string is required")
    try:
        value.encode("utf-8")
    except UnicodeError:
        invalid("invalid_text", "Text must be valid UTF-8")
    return value


def integer(value, *, minimum=1, maximum=2**31 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        invalid("invalid_integer", "Integer is outside the allowed range")
    return value


def opaque_id(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(UUID(value))
    except (ValueError, AttributeError):
        invalid("invalid_id", "An opaque resource identifier is required")


def _json_value(value, depth=0):
    if depth > 64:
        invalid("invalid_json", "Invalid JSON document")
    if value is None or type(value) in (bool, int):
        return
    if isinstance(value, str):
        value.encode("utf-8")
    elif type(value) is float:
        if not math.isfinite(value):
            invalid("invalid_json", "Invalid JSON document")
    elif isinstance(value, list):
        for item in value:
            _json_value(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                invalid("invalid_json", "Invalid JSON document")
            key.encode("utf-8")
            _json_value(item, depth + 1)
    else:
        invalid("invalid_json", "Invalid JSON document")


@dataclass(frozen=True, slots=True, init=False)
class FrozenJSON:
    """Own canonical bytes instead of retaining mutable input dictionaries."""
    json: str

    def __init__(self, value: Any):
        try:
            _json_value(value)
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            invalid("invalid_json", "Invalid JSON document")
        object.__setattr__(self, "json", encoded)

    @property
    def value(self):
        return json.loads(self.json)

    @property
    def sha256(self):
        return hashlib.sha256(self.json.encode("utf-8")).hexdigest()


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def relative_path(value: str) -> str:
    """Portable literal path/prefix, not a glob or a host resource capability.

    OS-level symlink/reparse/type checks still belong to workspace capture and
    every import/export boundary. Responsibility paths do not enforce isolation.
    """
    required_text(value)
    if value == ".":
        return value
    if value.startswith("/") or any(c in value for c in '\\:*?"<>|') or any(ord(c) < 32 or ord(c) == 127 for c in value):
        invalid("invalid_path", "Unsupported repository path")
    for part in value.split("/"):
        if part in ("", ".", "..") or part.endswith((".", " ")) or part.split(".", 1)[0].upper() in _RESERVED:
            invalid("invalid_path", "Unsupported repository path")
    return value


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    wall_seconds: int = 3600
    turn_seconds: int = 600
    command_seconds: int = 120
    model_calls: int = 100
    messages: int = 200
    no_progress: int = 10
    peer_wait_seconds: int = 300
    repair_attempts: int = 3
    repeated_failure_limit: int = 2

    def __post_init__(self):
        for key, value in asdict(self).items():
            integer(value, minimum=0 if key == "repair_attempts" else 1)

    @classmethod
    def from_dict(cls, value):
        return cls(**strict_fields(value, optional=[f.name for f in fields(cls)]))

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PolicySpec:
    execute: bool = False
    dependency_install: bool = False
    network: bool = False
    publish: bool = False
    model_parallelism: int = 1
    execution_parallelism: int = 2
    limits: BudgetLimits = field(default_factory=BudgetLimits)

    def __post_init__(self):
        for key in ("execute", "dependency_install", "network", "publish"):
            if type(getattr(self, key)) is not bool:
                invalid("invalid_capability", "Capabilities must be explicit booleans")
        integer(self.model_parallelism, maximum=4)
        integer(self.execution_parallelism, maximum=4)
        if not isinstance(self.limits, BudgetLimits):
            invalid("invalid_limits", "A validated budget policy is required")

    @classmethod
    def from_dict(cls, value):
        data = dict(strict_fields(value, optional=[f.name for f in fields(cls)]))
        if "limits" in data:
            data["limits"] = BudgetLimits.from_dict(data["limits"])
        return cls(**data)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MemberSpec:
    name: str
    responsibility: str
    goal: str
    orchestrator: bool
    paths: tuple[str, ...] = ()
    instructions: str = ""
    skills: tuple[str, ...] = ()

    def __post_init__(self):
        for key in ("name", "responsibility", "goal"):
            required_text(getattr(self, key))
        if type(self.orchestrator) is not bool:
            invalid("invalid_orchestrator", "Orchestrator selection must be a boolean")
        if not isinstance(self.instructions, str):
            invalid("invalid_instructions", "Instructions must be text")
        if self.instructions:
            required_text(self.instructions)
        for key in ("paths", "skills"):
            values = getattr(self, key)
            if not isinstance(values, (list, tuple)):
                invalid("invalid_list", "Paths and skill references must be arrays")
            converted = tuple(relative_path(item) if key == "paths" else required_text(item) for item in values)
            object.__setattr__(self, key, converted)

    @classmethod
    def from_dict(cls, value):
        return cls(**strict_fields(value, required=("name", "responsibility", "goal", "orchestrator"),
                                   optional=("paths", "instructions", "skills")))

    def to_dict(self):
        return {"name": self.name, "responsibility": self.responsibility, "goal": self.goal,
                "orchestrator": self.orchestrator, "paths": list(self.paths),
                "instructions": self.instructions, "skills": list(self.skills)}


@dataclass(frozen=True, slots=True)
class TeamSpec:
    name: str
    goal: str
    criteria: tuple[str, ...]
    members: tuple[MemberSpec, ...]
    policy: PolicySpec = field(default_factory=PolicySpec)

    def __post_init__(self):
        required_text(self.name); required_text(self.goal)
        if not isinstance(self.criteria, (list, tuple)) or not self.criteria:
            invalid("invalid_criteria", "At least one acceptance criterion is required")
        object.__setattr__(self, "criteria", tuple(required_text(item) for item in self.criteria))
        if not isinstance(self.members, (list, tuple)) or not 2 <= len(self.members) <= 4:
            invalid("invalid_members", "A team requires two to four members")
        if any(not isinstance(item, MemberSpec) for item in self.members):
            invalid("invalid_members", "Validated member definitions are required")
        object.__setattr__(self, "members", tuple(self.members))
        if sum(item.orchestrator for item in self.members) != 1:
            invalid("invalid_orchestrator", "Exactly one orchestrator is required")
        if not isinstance(self.policy, PolicySpec):
            invalid("invalid_policy", "A validated policy is required")
        if max(self.policy.model_parallelism, self.policy.execution_parallelism) > len(self.members):
            invalid("invalid_parallelism", "Concurrency cannot exceed team membership")

    @classmethod
    def from_dict(cls, value):
        data = strict_fields(value, required=("name", "goal", "criteria", "members"), optional=("policy",))
        if not isinstance(data["members"], list):
            invalid("invalid_members", "Members must be an array")
        return cls(name=data["name"], goal=data["goal"], criteria=data["criteria"],
                   members=tuple(MemberSpec.from_dict(item) for item in data["members"]),
                   policy=PolicySpec.from_dict(data.get("policy", {})))

    def to_dict(self):
        return {"name": self.name, "goal": self.goal, "criteria": list(self.criteria),
                "members": [item.to_dict() for item in self.members], "policy": self.policy.to_dict()}


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    id: str
    version: int
    spec: MemberSpec

    def __post_init__(self):
        object.__setattr__(self, "id", opaque_id(self.id))
        integer(self.version)
        if not isinstance(self.spec, MemberSpec):
            invalid("invalid_definition", "A validated member definition is required")

    @classmethod
    def create(cls, spec: MemberSpec):
        return cls(str(uuid4()), 1, spec)

    def revise(self, spec: MemberSpec, *, expected_version: int):
        integer(expected_version)
        if expected_version != self.version:
            raise CollaborationError(409, "stale_revision", "Definition version has changed")
        return AgentDefinition(self.id, self.version + 1, spec)


@dataclass(frozen=True, slots=True)
class MemberSnapshot:
    id: str
    team_run_id: str
    definition_id: str
    definition_version: int
    spec: MemberSpec
    policy_hash: str

    def __post_init__(self):
        for key in ("id", "team_run_id", "definition_id"):
            object.__setattr__(self, key, opaque_id(getattr(self, key)))
        integer(self.definition_version)
        if not isinstance(self.spec, MemberSpec):
            invalid("invalid_snapshot", "A validated member snapshot is required")
        if not isinstance(self.policy_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", self.policy_hash):
            invalid("invalid_policy_hash", "An exact policy hash is required")

    @classmethod
    def capture(cls, team_run_id: str, definition: AgentDefinition, policy_hash: str):
        if not isinstance(definition, AgentDefinition):
            invalid("invalid_definition", "A validated agent definition is required")
        return cls(str(uuid4()), team_run_id, definition.id, definition.version, definition.spec, policy_hash)
