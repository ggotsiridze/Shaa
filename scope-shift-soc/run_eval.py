"""Run the pilot evaluation.

    python run_eval.py --provider mock --model mock-perfect --tasks tasks/development --trials 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scopeshift.errors import TaskValidationError
from scopeshift.harness import DEFAULT_MAX_ATTEMPTS, run_eval
from scopeshift.loader import load_tasks
from scopeshift.metrics import compute_metrics
from scopeshift.oracle import check_against_reference
from scopeshift.providers.mock import MOCK_MODELS, MockProvider

ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", required=True, choices=["mock"])
    ap.add_argument("--model", required=True, help=f"mock models: {', '.join(MOCK_MODELS)}")
    ap.add_argument("--tasks", required=True, type=Path)
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    ap.add_argument("--backoff", type=float, default=0.0, help="base backoff seconds between retries")
    ap.add_argument("--results-dir", type=Path, default=ROOT / "results")
    ap.add_argument("--transcripts-dir", type=Path, default=ROOT / "transcripts")
    args = ap.parse_args(argv)

    if args.trials < 1 or args.max_attempts < 1:
        print("ERROR: --trials and --max-attempts must be >= 1", file=sys.stderr)
        return 2
    try:
        tasks = load_tasks(args.tasks)
    except TaskValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    problems = [p for t in tasks for p in check_against_reference(t)]
    if problems:
        print("ERROR: oracle/reference mismatch, refusing to run:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    if args.model not in MOCK_MODELS:
        print(f"ERROR: unknown mock model {args.model!r}", file=sys.stderr)
        return 2

    provider = MockProvider.from_tasks(args.model, tasks, settings={"temperature": None})
    run_id, records = run_eval(tasks, provider, args.trials, max_attempts=args.max_attempts, backoff_s=args.backoff)
    metrics = compute_metrics(records, tasks)

    args.results_dir.mkdir(parents=True, exist_ok=True)
    args.transcripts_dir.mkdir(parents=True, exist_ok=True)
    with (args.transcripts_dir / f"{run_id}.jsonl").open("x", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r.to_dict()) + "\n")
    summary = {"run_id": run_id, "provider": args.provider, "model": args.model,
               "tasks_dir": str(args.tasks), "trials": args.trials, "max_attempts": args.max_attempts,
               "metrics": metrics}
    (args.results_dir / f"{run_id}.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"run_id: {run_id}  model: {args.model}  trials/task: {args.trials}")
    print(f"{'task':<13} {'trial':>5} {'pass':>5} {'score':>6} {'att':>4}  failure_types")
    for r in records:
        print(f"{r.task_id:<13} {r.trial_index:>5} {r.strict_pass!s:>5} "
              f"{(r.score if r.score is not None else float('nan')):>6.2f} {r.attempt_count:>4}  {','.join(r.failure_types) or '-'}")
    k = args.trials
    keys = ["task_pass_rate", "pair_pass_rate", f"strict_pass_pow_{k}", f"pair_pass_pow_{k}", f"strict_pass_at_{k}_any",
            "exception_state_accuracy", "transition_event_accuracy", "governing_rule_accuracy",
            "primary_action_accuracy", "evidence_coverage", "source_authority_pair_pass_rate",
            "authority_contrast_consistency",
            "premature_termination_rate", "missed_termination_rate", "stale_authorization_rate",
            "parse_failure_rate", "infrastructure_failure_rate", "failure_type_counts"]
    labels = {f"strict_pass_pow_{k}": f"strict pass^{k} (all {k} trials pass)",
              f"pair_pass_pow_{k}": f"pair pass^{k} (both variants, all {k} trials)",
              f"strict_pass_at_{k}_any": f"strict pass@{k} (at least one trial passes; reference only)"}
    for key in keys:
        print(f"  {labels.get(key, key)}: {metrics[key]}")
    print(f"results: {args.results_dir / (run_id + '.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
