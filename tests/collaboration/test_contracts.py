"""Strict, immutable team input and member snapshot contracts."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import math
from unittest import TestCase
from uuid import uuid4

from pi_deepagent.collaboration.contracts import (
    AgentDefinition, BudgetLimits, CollaborationError, FrozenJSON, MemberSnapshot,
    MemberSpec, PolicySpec, TeamSpec, relative_path,
)


def member(name="worker", orchestrator=False):
    return {"name": name, "responsibility": "implement product", "goal": "pass required tests",
            "orchestrator": orchestrator, "paths": ["src"]}


def team():
    return {"name": "engineering", "goal": "ship correct product", "criteria": ["required gates pass"],
            "members": [member("lead", True), member("implementer"), member("verifier")],
            "policy": {"execute": True, "publish": True}}


class ImmutableDocumentTests(TestCase):
    def test_canonical_hash_copies_nested_input_and_returned_data(self):
        source = {"b": [1, {"name": "中文"}], "a": True}
        doc = FrozenJSON(source)
        equal = FrozenJSON({"a": True, "b": [1, {"name": "中文"}]})
        self.assertEqual(doc.sha256, equal.sha256)
        self.assertEqual(doc.json, '{"a":true,"b":[1,{"name":"中文"}]}')
        source["b"][1]["name"] = "changed"
        doc.value["b"].append("caller changed copy")
        self.assertEqual(doc.value, {"a": True, "b": [1, {"name": "中文"}]})
        with self.assertRaises(FrozenInstanceError):
            doc.json = "overwrite"

    def test_invalid_json_never_echoes_values_or_silently_coerces_keys(self):
        cycle = []; cycle.append(cycle)
        for value in ({1: "bad key"}, {"value": math.nan}, {"value": math.inf}, {"secret": object()},
                      {"secret": "\ud800"}, {"value": (1, 2)}, cycle):
            with self.subTest(kind=type(value)), self.assertRaises(CollaborationError) as raised:
                FrozenJSON(value)
            self.assertEqual(raised.exception.status, 400)
            self.assertEqual(raised.exception.code, "invalid_json")
            self.assertNotIn("secret", str(raised.exception))


class TeamInputTests(TestCase):
    def test_three_members_roundtrip_without_owner_role_or_credentials(self):
        value = team()
        spec = TeamSpec.from_dict(value)
        self.assertEqual([m.name for m in spec.members], ["lead", "implementer", "verifier"])
        self.assertEqual(sum(m.orchestrator for m in spec.members), 1)
        self.assertTrue(spec.policy.execute)
        self.assertFalse(spec.policy.network)
        self.assertFalse(spec.policy.dependency_install)
        self.assertEqual(TeamSpec.from_dict(spec.to_dict()), spec)
        value["criteria"].append("mutated")
        value["members"][0]["paths"].append("tests")
        self.assertEqual(spec.criteria, ("required gates pass",))
        self.assertEqual(spec.members[0].paths, ("src",))
        with self.assertRaises(FrozenInstanceError):
            spec.goal = "new goal"

    def test_invalid_member_count_or_orchestrator_rejected(self):
        bad = []
        for count in (0, 1, 5):
            value = team(); value["members"] = [member(str(i), i == 0) for i in range(count)]; bad.append(value)
        for lead_count in (0, 2, 3):
            value = team()
            for i, item in enumerate(value["members"]): item["orchestrator"] = i < lead_count
            bad.append(value)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(CollaborationError) as raised:
                TeamSpec.from_dict(value)
            self.assertEqual(raised.exception.status, 400)

    def test_unknown_fields_rejected_at_every_nested_boundary(self):
        for layer, key in (("team", "owner"), ("team", "role"), ("team", "workspace"),
                           ("member", "id"), ("member", "model"), ("member", "api_key"),
                           ("policy", "docker_flags"), ("policy", "image"), ("policy", "apply"),
                           ("limits", "ignore_required_tests")):
            value = team()
            target = value if layer == "team" else (value["members"][0] if layer == "member" else value["policy"])
            if layer == "limits":
                target = value["policy"].setdefault("limits", {})
            target[key] = "SEEDED-SECRET"
            with self.subTest(layer=layer, key=key), self.assertRaises(CollaborationError) as raised:
                TeamSpec.from_dict(value)
            self.assertEqual(raised.exception.code, "invalid_fields")
            self.assertNotIn("SEEDED-SECRET", str(raised.exception))

    def test_required_text_criteria_types_and_exact_booleans(self):
        for key, invalid in (("name", " "), ("goal", None), ("criteria", []), ("criteria", "text"),
                             ("criteria", [""]), ("members", {})):
            value = team(); value[key] = invalid
            with self.subTest(key=key), self.assertRaises(CollaborationError):
                TeamSpec.from_dict(value)
        for key, invalid in (("orchestrator", 1), ("goal", ""), ("paths", "src"), ("instructions", 12),
                             ("skills", [None])):
            value = member(); value[key] = invalid
            with self.subTest(key=key), self.assertRaises(CollaborationError):
                MemberSpec.from_dict(value)

    def test_capability_flags_are_deny_by_default_and_concurrency_is_bounded(self):
        policy = PolicySpec.from_dict({})
        self.assertEqual([policy.execute, policy.dependency_install, policy.network, policy.publish], [False] * 4)
        self.assertEqual((policy.model_parallelism, policy.execution_parallelism), (1, 2))
        for key, value in (("execute", "true"), ("network", 1), ("dependency_install", None),
                           ("model_parallelism", True), ("execution_parallelism", 0), ("model_parallelism", 5)):
            with self.subTest(key=key), self.assertRaises(CollaborationError):
                PolicySpec.from_dict({key: value})
        value = team(); value["policy"]["execution_parallelism"] = 4
        with self.assertRaises(CollaborationError): TeamSpec.from_dict(value)

    def test_budget_defaults_and_zero_repairs_do_not_create_unbounded_limits(self):
        limits = BudgetLimits.from_dict({})
        self.assertEqual((limits.wall_seconds, limits.turn_seconds, limits.command_seconds), (3600, 600, 120))
        self.assertEqual((limits.model_calls, limits.messages, limits.no_progress, limits.peer_wait_seconds), (100, 200, 10, 300))
        self.assertEqual((limits.repair_attempts, limits.repeated_failure_limit), (3, 2))
        self.assertEqual(BudgetLimits.from_dict({"repair_attempts": 0}).repair_attempts, 0)
        for key, value in (("model_calls", 0), ("wall_seconds", -1), ("messages", True),
                           ("command_seconds", 1.5), ("repair_attempts", -1)):
            with self.subTest(key=key), self.assertRaises(CollaborationError):
                BudgetLimits.from_dict({key: value})


class SnapshotAndPathTests(TestCase):
    def test_definition_edits_cannot_mutate_active_member_snapshot(self):
        definition = AgentDefinition.create(MemberSpec.from_dict(member("original")))
        run = str(uuid4())
        policy_hash = FrozenJSON(PolicySpec.from_dict({}).to_dict()).sha256
        snapshot = MemberSnapshot.capture(run, definition, policy_hash)
        edited = definition.revise(MemberSpec.from_dict(member("edited")), expected_version=1)
        self.assertEqual((definition.version, edited.version), (1, 2))
        self.assertEqual(definition.id, edited.id)
        self.assertEqual(snapshot.definition_version, 1)
        self.assertEqual(snapshot.spec.name, "original")
        self.assertNotEqual(snapshot.id, definition.id)
        self.assertEqual(snapshot.team_run_id, run)
        with self.assertRaises(CollaborationError) as raised:
            edited.revise(definition.spec, expected_version=1)
        self.assertEqual((raised.exception.status, raised.exception.code), (409, "stale_revision"))
        other = MemberSnapshot.capture(str(uuid4()), edited, policy_hash)
        self.assertNotEqual(other.id, snapshot.id)
        self.assertNotEqual(other.team_run_id, snapshot.team_run_id)
        self.assertEqual(other.spec.name, "edited")

    def test_opaque_server_ids_versions_and_policy_hash_are_validated(self):
        spec = MemberSpec.from_dict(member())
        for call in (lambda: AgentDefinition("model-chosen-id", 1, spec),
                     lambda: AgentDefinition(str(uuid4()), True, spec),
                     lambda: MemberSnapshot.capture("bad-run", AgentDefinition.create(spec), "a" * 64),
                     lambda: MemberSnapshot.capture(str(uuid4()), AgentDefinition.create(spec), "bad-policy")):
            with self.assertRaises(CollaborationError): call()

    def test_relative_paths_reject_windows_aliases_traversal_and_globs(self):
        self.assertEqual(relative_path("."), ".")
        self.assertEqual(relative_path("src/中文"), "src/中文")
        for value in ("", "/src", "C:/src", "src/../secret", "src\\file", "//host/share", "src//file",
                      "CON", "src/nul.txt", "COM1", "AUX", "file.", "file ", "src:stream", "src/**", "src/\0"):
            with self.subTest(value=value), self.assertRaises(CollaborationError):
                relative_path(value)
