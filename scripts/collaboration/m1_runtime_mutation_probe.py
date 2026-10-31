"""Isolated in-memory trust checks for run, budgets, mailbox and user questions."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


CASES = [
    ("exact_actor_grant", "runs", 'grant["binding_json"] != FrozenJSON(asdict(binding)).json', 'False'),
    ("wall_accumulation", "runs", 'wall_used_ms=wall_used_ms+?', 'wall_used_ms=?'),
    ("budget_boundary", "budgets", 'totals["used"] + totals["reserved"] + amount > limit', 'totals["used"] + totals["reserved"] + amount >= limit'),
    ("unknown_actor_guard", "budgets", 'if self.pending_for_actor(db, binding):', 'if False:'),
    ("unknown_not_released", "budgets", 'status = "unknown" if certainty == "unknown"', 'status = "released" if certainty == "unknown"'),
    ("pending_operations_block_finish", "mailbox", 'if outcome != "interrupted" and pending:', 'if False:'),
    ("late_message_preserved", "mailbox", "UPDATE collab_message_deliveries SET status='processed' WHERE turn_id=? AND status='delivered'",
     "UPDATE collab_message_deliveries SET status='processed' WHERE turn_id=? OR status='accepted'"),
    ("attempt_delivery_fence", "mailbox", "AND d.status='accepted' AND m.attempt_id=? ", "AND d.status='accepted' AND (m.attempt_id=? OR 1=1) "),
    ("message_redaction", "mailbox", 'self.redactor.clean(value["body"])', 'value["body"]'),
    ("exact_content_dedupe", "mailbox", 'fingerprint = FrozenJSON(payload).sha256',
     'fingerprint = FrozenJSON({**payload, "body": self.redactor.clean(value["body"])}).sha256'),
    ("question_expiration", "questions", 'if self.runs.clock_ms() >= row["expires_ms"]:', 'if False:'),
    ("question_target_version", "questions", 'if row["policy_version"] != run["policy_version"] or not self._target_current(db, row):', 'if False:'),
    ("question_parent_scope", "questions", 'if row["run_id"] != run_id:', 'if False:'),
    ("question_message_budget", "questions", 'reserve_in_transaction(db, binding, request_id, "messages", 1)',
     'reserve_in_transaction(db, binding, request_id, "model_calls", 1)'),
    ("answer_not_work_completion", "questions", "SET status='ready',revision=revision+1", "SET status='completed',revision=revision+1"),
    ("answer_pending_guard", "questions", 'if db.execute("SELECT 1 FROM collab_answer_deliveries WHERE work_attempt_id=? AND status!=\'processed\'", (work_attempt_id,)).fetchone():', 'if False:'),
    ("answer_processed_after_turn", "mailbox", "UPDATE collab_answer_deliveries SET status='processed' WHERE turn_id=? AND status='delivered'",
     "UPDATE collab_answer_deliveries SET status='processed' WHERE turn_id=? AND status='accepted'"),
    ("new_dispatch_rejects_old_policy", "runs", 'allow_historical_policy=False):', 'allow_historical_policy=True):'),
    ("old_operation_can_settle", "budgets", 'run = self.runs.authorize_binding(db, binding, allow_historical_policy=True)',
     'run = self.runs.authorize_binding(db, binding, allow_historical_policy=False)'),
    ("extension_preserves_reserved", "policies", 'policy["limits"].update(new)',
     'db.execute("UPDATE collab_budget_totals SET reserved=0 WHERE run_id=?", (run_id,)); policy["limits"].update(new)'),
    ("approval_target_version", "policies", 'if row["attempt_id"] != run["current_attempt_id"] or row["policy_version"] != run["policy_version"]:', 'if False:'),
    ("approval_expiration", "policies", 'if runs.clock_ms() >= row["expires_ms"]:', 'if False:'),
    ("denial_is_not_approval", "policies", 'if value["decision"] == "approve":', 'if True:'),
    ("approval_parent_scope", "policies", 'if row["run_id"] != run_id:', 'if False:'),
    ("approval_does_not_resume", "policies", 'policy["limits"].update(new)',
     'db.execute("UPDATE collab_runs SET status=\'running\' WHERE id=?", (run_id,)); policy["limits"].update(new)'),
    ("policy_revision_cas", "policies", 'WHERE id=? AND policy_version=? AND revision=? AND status=?',
     'WHERE id=? AND policy_version=? AND revision>=? AND status=?'),
]

CHILD = r'''
import importlib, json, pathlib, sys, unittest
name, old, new = sys.argv[1:4]
if name:
    module = importlib.import_module("pi_deepagent.collaboration." + name)
    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    assert source.count(old) == 1, "Mutation no longer matches exactly one source location"
    exec(compile(source.replace(old, new, 1), module.__file__, "exec"), module.__dict__)
suite = unittest.TestSuite()
for module in sys.argv[4:]:
    suite.addTests(unittest.defaultTestLoader.loadTestsFromName(module))
result = unittest.TextTestRunner(verbosity=1).run(suite)
print("MUTATION_TEST_RESULT " + json.dumps({"ran":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),"skipped":len(result.skipped)}))
sys.exit(0 if result.wasSuccessful() else 1)
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    for name, module, old, new in [("baseline", "", "", ""), *CASES]:
        modules = {"runs": ["tests.collaboration.test_runs"], "budgets": ["tests.collaboration.test_runs", "tests.collaboration.test_mailbox"],
                   "mailbox": ["tests.collaboration.test_mailbox", "tests.collaboration.test_questions"], "questions": ["tests.collaboration.test_questions"],
                   "policies": ["tests.collaboration.test_policies"]}
        selected = modules.get(module, ["tests.collaboration.test_runs", "tests.collaboration.test_mailbox", "tests.collaboration.test_questions", "tests.collaboration.test_policies"])
        if name in ("new_dispatch_rejects_old_policy", "old_operation_can_settle"):
            selected = ["tests.collaboration.test_policies"]
        result = subprocess.run([sys.executable, "-c", CHILD, module, old, new, *selected], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=60,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        (args.output / (name + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
        if not old and result.returncode:
            raise SystemExit("Baseline failed; mutation score is not meaningful")
        if "Mutation no longer matches exactly one" in result.stderr or result.returncode not in (0, 1):
            raise SystemExit("Mutation source drift or unexpected child error; do not count as killed")
        summaries = [line.removeprefix("MUTATION_TEST_RESULT ") for line in result.stdout.splitlines() if line.startswith("MUTATION_TEST_RESULT ")]
        if len(summaries) != 1:
            raise SystemExit("No completed unittest result; import or mutation setup failure is not a killed mutant")
        summary = json.loads(summaries[0])
        if summary["skipped"] or summary["ran"] == 0:
            raise SystemExit("Skipped or empty mutation suite cannot establish trust")
        status = "baseline_passed" if not old else ("killed" if result.returncode else "survived")
        records.append({"name": name, "module": module, "old": old, "new": new, "status": status,
                        "test_modules": selected, "test_result": summary})
        print(name + ": " + status, flush=True)
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(), "method": "in-memory manual spot-check",
              "records": records, "killed": sum(r["status"] == "killed" for r in records), "total": len(CASES)}
    (args.output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["killed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
