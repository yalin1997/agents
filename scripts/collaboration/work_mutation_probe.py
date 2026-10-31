"""In-memory work-planning mutation spot checks; never change source files."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

CASES = [
    ("orchestrator", "work", 'json.loads(row["spec_json"]).get("orchestrator") is not True', 'False'),
    ("context_binding", "work", 'or row["binding_json"]!=FrozenJSON(asdict(context.binding)).json', 'or False'),
    ("dependency_cycle", "work", 'if visited!=len(graph):', 'if False:'),
    ("required_work", "work", 'VALUES (?,?,?,?,?,?,1,?)', 'VALUES (?,?,?,?,?,?,0,?)'),
    ("all_source_roots", "work", 'roots.update(row[0] for row in inherited)', 'roots.update(row[0] for row in inherited[:1])'),
    ("attempt_cas", "work", 'if changed.rowcount!=1: conflict("stale_revision","Work changed during attempt preparation")', 'pass'),
    ("old_attempt_bypass", "work", 'if db.execute("SELECT 1 FROM collab_work_attempts WHERE work_id=?",(work_id,)).fetchone():', 'if False:'),
    ("unknown_actor", "work", 'if BudgetRepository.pending_for_actor(db,binding):', 'if False:'),
    ("work_redaction", "work", '"goal":self.redactor.clean(objective["goal"])', '"goal":objective["goal"]'),
    ("unknown_work_continue", "runs", 'if db.execute("SELECT 1 FROM collab_member_turns WHERE run_id=? AND (status IN (\'queued\',\'running\') OR certainty=\'unknown\')",(run_id,)).fetchone() or self._unknown_work(db,run_id):', 'if False:'),
    ("migration_repair_reset", "migration", 'CHECK(value=0)', 'CHECK(value>=0)'),
]

CHILD = r'''
import importlib,json,pathlib,sys,unittest
class MeasuredResult(unittest.TextTestResult):
    infrastructure_errors=0
    def addError(self,test,err):
        frames=[]; trace=err[2]
        while trace:
            frames.append(trace.tb_frame.f_code.co_name); trace=trace.tb_next
        if any(name in frames for name in ("_callSetUp","_callTearDown","setUpClass","tearDownClass")) or isinstance(test,unittest.loader._FailedTest):
            self.infrastructure_errors+=1
        super().addError(test,err)
name,old,new=sys.argv[1:4]
if name=="migration":
    module=importlib.import_module("pi_deepagent.collaboration.storage")
    target=module.MIGRATIONS[5]
    source=target.read_bytes().decode("utf-8")
    assert source.count(old)==1,"Mutation source no longer unique"
    class MutatedMigration:
        def read_bytes(self): return source.replace(old,new,1).encode("utf-8")
    module.MIGRATIONS=module.MIGRATIONS[:5]+(MutatedMigration(),)+module.MIGRATIONS[6:]
elif name:
    module=importlib.import_module("pi_deepagent.collaboration."+name)
    source=pathlib.Path(module.__file__).read_text(encoding="utf-8")
    assert source.count(old)==1,"Mutation source no longer unique"
    exec(compile(source.replace(old,new,1),module.__file__,"exec"),module.__dict__)
suite=unittest.defaultTestLoader.loadTestsFromName("tests.collaboration.test_work")
result=unittest.TextTestRunner(verbosity=1,resultclass=MeasuredResult).run(suite)
print("MUTATION_TEST_RESULT "+json.dumps({"ran":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),"infrastructure_errors":result.infrastructure_errors,"skipped":len(result.skipped)}))
sys.exit(0 if result.wasSuccessful() else 1)
'''


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    args.output.mkdir(parents=True,exist_ok=False)
    records=[]
    for name,module,old,new in [("baseline","","",""),*CASES]:
        result=subprocess.run([sys.executable,"-c",CHILD,module,old,new],capture_output=True,text=True,
                              encoding="utf-8",errors="replace",timeout=90,env={**os.environ,"PYTHONIOENCODING":"utf-8"})
        (args.output/(name+".log")).write_text(result.stdout+result.stderr,encoding="utf-8")
        markers=[line.removeprefix("MUTATION_TEST_RESULT ") for line in result.stdout.splitlines() if line.startswith("MUTATION_TEST_RESULT ")]
        if result.returncode not in (0,1) or len(markers)!=1 or "Mutation source no longer unique" in result.stderr:
            raise SystemExit("No completed suite or source drift; do not count as killed")
        summary=json.loads(markers[0])
        if summary["ran"]!=27 or summary["skipped"] or (not old and result.returncode):
            raise SystemExit("Baseline, wrong suite size or skips cannot establish trust")
        # Infrastructure/import/setup errors are not mutation evidence.
        if summary["infrastructure_errors"]:
            raise SystemExit("Import/setup/teardown errors cannot establish trust")
        status="baseline_passed" if not old else ("killed" if result.returncode else "survived")
        records.append({"name":name,"module":module,"old":old,"new":new,"status":status,"test_result":summary})
        print(name+": "+status,flush=True)
    report={"recorded_at":datetime.now(timezone.utc).isoformat(),"method":"in-memory manual work spot-check",
            "records":records,"killed":sum(r["status"]=="killed" for r in records),"total":len(CASES)}
    (args.output/"results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return 0 if report["killed"]==report["total"] else 1


if __name__=="__main__":
    raise SystemExit(main())
