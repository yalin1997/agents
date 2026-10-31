"""In-memory mutation spot-checks of real draft-team HTTP authorization."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


CASES = [
    ("outer_admin", "pi_deepagent.web.app", "if session.role != 'admin' or session.name != 'admin':", "if False:"),
    ("patch_csrf", "pi_deepagent.web.app", "if session and not hmac.compare_digest(", "if method == 'POST' and session and not hmac.compare_digest("),
    ("patch_origin", "pi_deepagent.web.app", "if headers.get('origin') != self.origin:", "if method == 'POST' and headers.get('origin') != self.origin:"),
    ("patch_body_limit", "pi_deepagent.web.app", "if size > 128*1024:", "if method == 'POST' and size > 128*1024:"),
    ("created_resource_audit", "pi_deepagent.collaboration.web_adapter", 'scope["_audit_created_team_id"] = result["id"]', 'pass'),
    ("operation_permission", "pi_deepagent.collaboration.access", 'or action not in self.allowed_actions', 'or False'),
    ("resource_scope", "pi_deepagent.collaboration.storage", 'if row["owner"] != scope.owner or row["workspace"] != scope.workspace:', 'if False:'),
    ("idempotent_replay", "pi_deepagent.collaboration.storage", 'if prior is not None:', 'if False:'),
]

CHILD = r'''
import importlib,json,pathlib,sys,unittest
name,old,new=sys.argv[1:4]
if name:
    module=importlib.import_module(name)
    source=pathlib.Path(module.__file__).read_text(encoding="utf-8")
    assert source.count(old)==1,"Mutation source no longer unique"
    exec(compile(source.replace(old,new,1),module.__file__,"exec"),module.__dict__)
suite=unittest.defaultTestLoader.loadTestsFromNames(sys.argv[4:])
result=unittest.TextTestRunner(verbosity=1).run(suite)
print("MUTATION_TEST_RESULT "+json.dumps({"ran":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),"skipped":len(result.skipped)}))
sys.exit(0 if result.wasSuccessful() else 1)
'''


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    args.output.mkdir(parents=True,exist_ok=False)
    records=[]
    for name,module,old,new in [("baseline","","",""),*CASES]:
        modules=["tests.collaboration.test_team_http"]
        if name in ("baseline","outer_admin"): modules.append("tests.test_web_app")
        result=subprocess.run([sys.executable,"-c",CHILD,module,old,new,*modules],capture_output=True,text=True,
                              encoding="utf-8",errors="replace",timeout=90,env={**os.environ,"PYTHONIOENCODING":"utf-8"})
        (args.output/(name+".log")).write_text(result.stdout+result.stderr,encoding="utf-8")
        markers=[line.removeprefix("MUTATION_TEST_RESULT ") for line in result.stdout.splitlines() if line.startswith("MUTATION_TEST_RESULT ")]
        if result.returncode not in (0,1) or len(markers)!=1 or "Mutation source no longer unique" in result.stderr:
            raise SystemExit("No completed suite or mutation source drift; do not count as killed")
        summary=json.loads(markers[0])
        if not summary["ran"] or summary["skipped"] or (not old and result.returncode):
            raise SystemExit("Baseline, empty or skipped suite cannot establish trust")
        status="baseline_passed" if not old else ("killed" if result.returncode else "survived")
        records.append({"name":name,"module":module,"old":old,"new":new,"status":status,"test_modules":modules,"test_result":summary})
        print(name+": "+status,flush=True)
    report={"recorded_at":datetime.now(timezone.utc).isoformat(),"method":"in-memory manual HTTP spot-check",
            "records":records,"killed":sum(record["status"]=="killed" for record in records),"total":len(CASES)}
    (args.output/"results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return 0 if report["killed"]==report["total"] else 1


if __name__=="__main__":
    raise SystemExit(main())
