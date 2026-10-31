"""In-memory root-budget scope, conservation, CAS and drain-barrier checks."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.collaboration.typed_approval_mutation_probe import CHILD, main

ROOT_CHILD = CHILD.replace('loadTestsFromName("tests.collaboration.test_typed_approvals")',
                           'loadTestsFromName("tests.collaboration.test_repair_limits")')
CASES = [
    ("increase_required","repair_limits",'if new["repair_attempts"]<=old["repair_attempts"]:','if new["repair_attempts"]<old["repair_attempts"]:'),
    ("exact_root_required","repair_limits",'if kind=="budget_extension" and root_values!=(root_id is not None):','if False:'),
    ("related_work_scope","repair_limits",'if work_id is not None and not db.execute("SELECT 1 FROM collab_work_roots WHERE work_id=? AND run_id=? AND root_id=?",(work_id,run_id,root_id)).fetchone():','if False:'),
    ("run_scope","repair_limits",'SELECT * FROM collab_repair_roots WHERE id=? AND run_id=?','SELECT * FROM collab_repair_roots WHERE id=? AND ? IS NOT NULL'),
    ("reserved_conservation","repair_limits",'SET limit_total=?,revision=revision+1','SET limit_total=?,reserved=0,revision=revision+1'),
    ("root_cas","repair_limits",'if changed.rowcount!=1:','if False:'),
    ("other_root_defaults","policies",'if kind=="budget_extension" and root_id is None:','if kind=="budget_extension":'),
    ("stop_drain","runs",'if db.execute("SELECT 1 FROM collab_repair_roots WHERE run_id=? AND reserved>0",(run_id,)).fetchone():\n            conflict("resources_not_drained","Repair reservations are not reconciled")','if False:\n            conflict("resources_not_drained","Repair reservations are not reconciled")'),
    ("continue_drain","runs",'if db.execute("SELECT 1 FROM collab_repair_roots WHERE run_id=? AND reserved>0",(run_id,)).fetchone():\n                conflict("resources_not_drained","Repair reservations must be reconciled before continuation")','if False:\n                conflict("resources_not_drained","Repair reservations must be reconciled before continuation")'),
    ("old_baseline","policies",'if any(baseline[key]!=old[key] for key in old):','if False:'),
]


if __name__=='__main__':
    raise SystemExit(main(cases=CASES,child_source=ROOT_CHILD,suite_count=20,method='in-memory repair-limit spot-check'))
