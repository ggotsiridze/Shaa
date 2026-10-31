"""Validate every task: schema, cross-references, and oracle == hidden_reference."""

import sys
from pathlib import Path

from _common import ROOT

from scopeshift.errors import TaskValidationError
from scopeshift.loader import load_tasks
from scopeshift.oracle import check_against_reference, run_oracle


def main() -> int:
    task_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tasks" / "development"
    try:
        tasks = load_tasks(task_dir)
    except TaskValidationError as exc:
        print(f"FAIL: {exc}")
        return 1
    failed = False
    for t in tasks:
        problems = check_against_reference(t)
        res = run_oracle(t)
        status = "OK  " if not problems else "FAIL"
        print(f"{status} {t.task_id}: oracle -> {res.exception_status.value}, transition={res.transition_event_id}, "
              f"rule={res.governing_rule_id}, action={res.primary_action.value}, evidence={res.required_evidence_ids}, "
              f"rejected_claims={res.rejected_claims}")
        for p in problems:
            print(f"     {p}")
        failed |= bool(problems)
    print(f"{len(tasks)} tasks checked: {'FAIL' if failed else 'PASS'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
