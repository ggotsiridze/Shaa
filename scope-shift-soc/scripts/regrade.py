"""Re-grade saved model answers with the CURRENT parser and grader. Makes no API calls.

    python scripts/regrade.py transcripts/<run_id>.jsonl [--tasks tasks/development]

Reads each trial's `raw_response` exactly as the model returned it, re-parses and re-grades it,
and writes results/<run_id>.regraded.json next to the original summary (which is left untouched).
Infrastructure-failure trials stay infrastructure failures. The output records which grader
settings changed relative to the original run so the comparison is honest.
"""

import argparse
import json
import sys
from dataclasses import fields
from pathlib import Path

from _common import ROOT

from scopeshift.grader import ParseError, grade, parse_response
from scopeshift.harness import TrialRecord
from scopeshift.loader import load_tasks
from scopeshift.metrics import compute_metrics
from scopeshift.schema import MAX_RATIONALE_CHARS
from scopeshift.taxonomy import Failure, classify


def regrade_record(rec: TrialRecord, task) -> TrialRecord:
    if rec.infrastructure_failure:
        return rec
    rec.parse_error, rec.parsed_response, rec.components = None, None, None
    rec.strict_pass, rec.model_failure = False, False
    try:
        answer = parse_response(rec.raw_response)
    except ParseError as exc:
        rec.parse_error, rec.score, rec.model_failure = str(exc), 0.0, True
        rec.failure_types = [Failure.PARSE_FAILURE.value]
        return rec
    g = grade(task, answer)
    rec.parsed_response = answer.model_dump()
    rec.score, rec.components, rec.strict_pass = g.score, g.components, g.strict_pass
    rec.model_failure = not g.strict_pass
    rec.failure_types = [f.value for f in classify(task, answer, g.strict_pass)]
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcript", type=Path)
    ap.add_argument("--tasks", type=Path, default=ROOT / "tasks" / "development")
    ap.add_argument("--results-dir", type=Path, default=ROOT / "results")
    args = ap.parse_args()

    tasks = {t.task_id: t for t in load_tasks(args.tasks)}
    names = {f.name for f in fields(TrialRecord)}
    rows = [json.loads(line) for line in args.transcript.read_text(encoding="utf-8").splitlines() if line.strip()]
    before = [TrialRecord(**{k: v for k, v in r.items() if k in names}) for r in rows]
    after = [regrade_record(TrialRecord(**{k: v for k, v in r.items() if k in names}), tasks[r["task_id"]]) for r in rows]
    run_id = rows[0]["run_id"]
    used = [tasks[t] for t in dict.fromkeys(r["task_id"] for r in rows)]

    changed = [(b, a) for b, a in zip(before, after) if (b.strict_pass, b.failure_types) != (a.strict_pass, a.failure_types)]
    for b, a in changed:
        print(f"  {b.task_id} trial {b.trial_index}: pass {b.strict_pass} -> {a.strict_pass} "
              f"({','.join(b.failure_types) or '-'} -> {','.join(a.failure_types) or '-'})")
    m_before, m_after = compute_metrics(before, used), compute_metrics(after, used)
    k = m_after["trials_per_task"]
    for key in ("task_pass_rate", f"strict_pass_pow_{k}", f"pair_pass_pow_{k}", "parse_failure_rate"):
        print(f"  {key}: {m_before[key]} -> {m_after[key]}")

    out = {
        "run_id": run_id, "regraded_from": str(args.transcript), "api_calls_made": 0,
        "note": (f"Raw model outputs re-graded with the current grader (rationale limit {MAX_RATIONALE_CHARS} chars). "
                 "Outputs were produced under the prompt recorded in the original run manifest; if that prompt "
                 "stated a lower limit, this re-grade is more lenient than the instruction the model received."),
        "trials_changed": [{"task_id": b.task_id, "trial_index": b.trial_index, "before": b.failure_types or ["PASS"],
                            "after": a.failure_types or ["PASS"]} for b, a in changed],
        "metrics_before": m_before, "metrics_after": m_after,
    }
    args.results_dir.mkdir(parents=True, exist_ok=True)
    path = args.results_dir / f"{run_id}.regraded.json"
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"{len(changed)} trial(s) changed; wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
