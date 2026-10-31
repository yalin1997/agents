"""In-memory checks of typed approvals, scope, replay and migration integrity."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

# Share only the child harness/result classification; each probe owns its cases.
try:
    from scripts.collaboration.work_mutation_probe import CHILD as WORK_CHILD
except ModuleNotFoundError:
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    from scripts.collaboration.work_mutation_probe import CHILD as WORK_CHILD

CHILD = WORK_CHILD.replace('loadTestsFromName("tests.collaboration.test_work")',
                           'loadTestsFromName("tests.collaboration.test_typed_approvals")')
CHILD = CHILD.replace('name,old,new=sys.argv[1:4]',r'''
class CompleteMeasuredResult(MeasuredResult):
    def addFailure(self,test,err):
        trace=err[2]; frames=[]
        while trace:
            frames.append(trace.tb_frame.f_code.co_name); trace=trace.tb_next
        if any(name in frames for name in ("_callSetUp","_callTearDown","setUpClass","tearDownClass")):
            self.infrastructure_errors+=1
        super().addFailure(test,err)
name,old,new=sys.argv[1:4]
''').replace('resultclass=MeasuredResult','resultclass=CompleteMeasuredResult')
CASES = [
    ("runtime_support","policies",'if admitting and any(new[key] and not old[key] and key not in self.available_capabilities for key in old):','if False:'),
    ("exact_baseline","policies",'if any(baseline[key]!=old[key] for key in old):','if False:'),
    ("work_scope","policies",'WHERE run_id=? AND attempt_id=? AND work_id=? AND member_id=? ',
     'WHERE run_id=? AND attempt_id=? AND ? IS NOT NULL AND member_id=? '),
    ("member_scope","policies",'WHERE run_id=? AND attempt_id=? AND work_id=? AND member_id=? ',
     'WHERE run_id=? AND attempt_id=? AND work_id=? AND ? IS NOT NULL '),
    ("attempt_scope","policies",'WHERE run_id=? AND attempt_id=? AND work_id=? AND member_id=? ',
     'WHERE run_id=? AND ? IS NOT NULL AND work_id=? AND member_id=? '),
    ("path_boundary","policies",'path.startswith(prefix+"/")','path.startswith(prefix)'),
    ("decision_support_recheck","policies",'self._typed_change(kind,admitted_old,new,admitting=True)  # Recheck runtime support at decision time.','pass'),
    ("publication_cas","policies",'WHERE id=? AND policy_version=? AND revision=? AND status=?','WHERE id=? AND policy_version=? AND revision>=? AND status=?'),
    ("migration_foreign_keys","storage",'if db.execute("PRAGMA foreign_key_check").fetchone() is not None:','if False:'),
    ("replay_before_admission","policies",'def _typed_change(self, kind, old, new, *, admitting=False):','def _typed_change(self, kind, old, new, *, admitting=True):'),
]


def main(argv=None, *, cases=None, child_source=None, suite_count=23, method='in-memory typed approval spot-check'):
    cases = CASES if cases is None else cases
    child_source = CHILD if child_source is None else child_source
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    args.output.mkdir(parents=True,exist_ok=False)
    records=[]
    for name,module,old,new in [('baseline','','',''),*cases]:
        child=subprocess.run([sys.executable,'-c',child_source,module,old,new],capture_output=True,text=True,
                             encoding='utf-8',errors='replace',timeout=90,env={**os.environ,'PYTHONIOENCODING':'utf-8'})
        (args.output/(name+'.log')).write_text(child.stdout+child.stderr,encoding='utf-8')
        markers=[line.removeprefix('MUTATION_TEST_RESULT ') for line in child.stdout.splitlines() if line.startswith('MUTATION_TEST_RESULT ')]
        if child.returncode not in (0,1) or len(markers)!=1 or 'Mutation source no longer unique' in child.stderr:
            raise SystemExit('No completed suite or source drift; do not score')
        result=json.loads(markers[0])
        if result['ran']!=suite_count or result['skipped'] or result['infrastructure_errors'] or (not old and child.returncode):
            raise SystemExit('Baseline, import/setup/teardown or incomplete suite cannot establish trust')
        status='baseline_passed' if not old else ('killed' if child.returncode else 'survived')
        records.append({'name':name,'module':module,'old':old,'new':new,'status':status,'test_result':result})
        print(name+': '+status,flush=True)
    report={'recorded_at':datetime.now(timezone.utc).isoformat(),'method':method,
            'records':records,'killed':sum(r['status']=='killed' for r in records),'total':len(cases)}
    (args.output/'results.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return 0 if report['killed']==report['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
