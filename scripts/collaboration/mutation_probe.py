"""Targeted in-memory mutation audit; never edits repository source files.

Run from the repository root with its installed Python environment. The script
first requires the unmodified target suite to pass. Each child then replaces
one guard in module memory before loading the same tests. This is a limited
trust spot-check, not a comprehensive mutation score for collaboration.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


CASES = [
    ("thread_scope", 'if configurable.get("thread_id") != self.thread_id:', 'if False:'),
    ("checkpoint_immutability", 'if prior is not None and tuple(prior)[3:] != content:', 'if False:'),
    ("channel_version_immutability", 'if stored is not None and tuple(stored) != blob:', 'if False:'),
    ("normal_write_deduplication", 'verb = "REPLACE" if index < 0 else "IGNORE"', 'verb = "REPLACE"'),
    ("pending_write_order", 'ORDER BY task_path,task_id,write_index', 'ORDER BY task_path DESC,task_id,write_index'),
    ("private_existing_schema", 'elif not TABLES.issubset(tables):', 'elif False:'),
]

CHILD = r'''
import importlib, pathlib, sys, unittest
module = importlib.import_module("pi_deepagent.collaboration.checkpoint")
source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
old, new = sys.argv[1:3]
if old:
    assert source.count(old) == 1, "Mutation no longer matches exactly one source location"
    exec(compile(source.replace(old, new, 1), module.__file__, "exec"), module.__dict__)
suite = unittest.defaultTestLoader.discover("tests/collaboration", pattern="test_checkpoint.py")
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory")
    args = parser.parse_args(argv)
    if not Path("tests/collaboration/test_checkpoint.py").is_file():
        parser.error("Run from the repository root")
    args.output.mkdir(parents=True, exist_ok=False)
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    records = []
    for name, old, new in [("baseline", "", ""), *CASES]:
        result = subprocess.run([sys.executable, "-c", CHILD, old, new], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", env=environment, timeout=60)
        (args.output / (name + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
        if name == "baseline" and result.returncode:
            raise SystemExit("Unmodified checkpoint suite failed; mutation score is not meaningful")
        if old and "Mutation no longer matches exactly one" in result.stderr:
            raise SystemExit("Mutation source drift; do not count as killed")
        if result.returncode not in (0, 1):
            raise SystemExit("Unexpected child failure; inspect evidence")
        records.append({"name": name, "old": old, "new": new, "returncode": result.returncode,
                        "status": "baseline_passed" if not old else ("killed" if result.returncode else "survived")})
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(), "method": "in-memory manual spot-check",
              "module": "pi_deepagent.collaboration.checkpoint", "records": records,
              "killed": sum(r["status"] == "killed" for r in records), "total": len(CASES)}
    (args.output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["killed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
