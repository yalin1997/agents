"""In-memory objective identity, admission, history and snapshot spot checks."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.collaboration.typed_approval_mutation_probe import CHILD, main

CRITERIA_CHILD = CHILD.replace('loadTestsFromName("tests.collaboration.test_typed_approvals")',
                               'loadTestsFromName("tests.collaboration.test_criteria")')
CASES = [
    ("old_baseline","policies",'if any(baseline[key]!=old[key] for key in old):','if False:'),
    ("empty_criteria","criteria",'if not isinstance(value["criteria"],list) or not value["criteria"]:','if not isinstance(value["criteria"],list):'),
    ("canonical_hash","criteria",'"hash":FrozenJSON(payload).sha256','"hash":"0"*64'),
    ("current_version","criteria",'ORDER BY version DESC LIMIT 1','ORDER BY version ASC LIMIT 1'),
    ("work_target","criteria",'kind,identifier = target(db,run_id,work_id)','kind,identifier = target(db,run_id,None)'),
    ("old_snapshot_gate","criteria",'return snapshot(db,run_id,work_id)==value','return True'),
    ("snapshot_capture","work",'"criteria_refs":objectives.snapshot(db,binding.run_id,work_id)','"criteria_refs":{}'),
    ("work_cas","criteria",'if changed.rowcount!=1:','if False:'),
    ("work_read_redaction","work",'"goal":self.redactor.clean(objective["goal"])','"goal":objective["goal"]'),
    ("late_secret_admission","policies",'if admitting and self._clean_objective(new)!=new:','if False:'),
]


if __name__=='__main__':
    raise SystemExit(main(cases=CASES,child_source=CRITERIA_CHILD,method='in-memory criteria spot-check'))
