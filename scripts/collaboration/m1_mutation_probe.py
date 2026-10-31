"""In-memory M1 trust spot-check, leaving source and real data unchanged."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


CASES = [
    ("orchestrator_count", "contracts", 'if sum(item.orchestrator for item in self.members) != 1:', 'if False:'),
    ("capability_boolean", "contracts", 'if type(getattr(self, key)) is not bool:', 'if False:'),
    ("canonical_hash", "contracts", 'sort_keys=True', 'sort_keys=False'),
    ("state_revision", "state", 'if expected_revision != self.revision:', 'if False:'),
    ("completion_barrier", "state", 'if target == "completed" and not facts.complete:', 'if False:'),
    ("authorize_before_replay", "storage", 'with self.transaction() as db:\n            authorize(db)',
     'with self.transaction() as db:\n            pass'),
    ("rollback_atomicity", "storage", 'self._db.rollback()', 'self._db.commit()'),
    ("service_role", "access", 'if session.name != "admin" or session.role != "admin" or action not in self.allowed_actions:', 'if False:'),
    ("persistence_redaction", "service", 'return self.auth.redactor.clean(value)', 'return value'),
]

CHILD = r'''
import importlib, pathlib, sys, unittest
name, old, new = sys.argv[1:4]
if name:
    module = importlib.import_module("pi_deepagent.collaboration." + name)
    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    assert source.count(old) == 1, "Mutation no longer matches exactly one source location"
    exec(compile(source.replace(old, new, 1), module.__file__, "exec"), module.__dict__)
suite = unittest.TestSuite()
for pattern in ("test_contracts.py", "test_state.py", "test_storage.py", "test_team_service.py"):
    suite.addTests(unittest.defaultTestLoader.discover("tests/collaboration", pattern=pattern))
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    for name, module, old, new in [("baseline", "", "", ""), *CASES]:
        result = subprocess.run([sys.executable, "-c", CHILD, module, old, new], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=60,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        (args.output / (name + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
        if not old and result.returncode:
            raise SystemExit("Baseline failed; mutation score is not meaningful")
        if "Mutation no longer matches exactly one" in result.stderr or result.returncode not in (0, 1):
            raise SystemExit("Mutation source drift or unexpected child error; do not count as killed")
        records.append({"name": name, "module": module, "old": old, "new": new,
                        "status": "baseline_passed" if not old else ("killed" if result.returncode else "survived")})
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(), "method": "in-memory manual spot-check",
              "records": records, "killed": sum(r["status"] == "killed" for r in records), "total": len(CASES)}
    (args.output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["killed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
