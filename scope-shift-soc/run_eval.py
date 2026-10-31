"""Run the ScopeShift-SOC evaluation.

    python run_eval.py --provider mock   --model mock-perfect --tasks tasks/development --trials 3
    python run_eval.py --provider openai --model <MODEL> --tasks tasks/development --trials 5 --dry-run
    python run_eval.py --provider openai --model <MODEL> --tasks tasks/development --task-ids SS-DEV-001A --trials 1

Paid providers print the experiment plan and require confirmation (--yes, or an interactive
"y") before the first API call. --dry-run and --validate-api never generate model output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from scopeshift.errors import ConfigurationError, InfrastructureError, TaskValidationError
from scopeshift.harness import DEFAULT_MAX_ATTEMPTS, TrialRecord, run_eval
from scopeshift.loader import load_tasks
from scopeshift.metrics import compute_metrics
from scopeshift.oracle import check_against_reference
from scopeshift.prompt import DEFAULT_PROMPT_VERSION, PROMPT_VERSIONS, build_prompt
from scopeshift.providers import PROVIDER_NAMES, build_provider, provider_module
from scopeshift.providers.mock import MOCK_MODELS

ROOT = Path(__file__).resolve().parent


def _pkg(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generation_settings(args: argparse.Namespace) -> dict:
    if args.provider == "mock":
        return {"temperature": None}
    max_out = args.max_output_tokens
    if max_out is None and args.provider == "anthropic":  # required by the API: show the value actually sent
        from scopeshift.providers.anthropic_provider import DEFAULT_MAX_TOKENS
        max_out = DEFAULT_MAX_TOKENS
    return {
        "structured_output": args.structured_output,
        "temperature": args.temperature,
        "max_output_tokens": max_out,
        "reasoning_effort": args.reasoning_effort,
        "timeout_s": args.timeout,
    }


def planned_calls(n_tasks: int, trials: int) -> int:
    return n_tasks * trials


def plan_lines(args: argparse.Namespace, n_tasks: int, settings: dict) -> list[str]:
    calls = planned_calls(n_tasks, args.trials)
    lines = [
        f"Prompt: {args.prompt_version}",
        f"Provider: {args.provider}",
        f"Model: {args.model}",
        f"Tasks: {n_tasks}",
        f"Trials per task: {args.trials}",
        (f"Total model calls: {calls} (at most {calls * args.max_attempts} requests if every call is retried "
         f"{args.max_attempts - 1} times)"),
        f"Generation settings: {json.dumps(settings)}",
        f"Retries: max_attempts={args.max_attempts}, backoff={args.backoff}s, jitter={args.jitter}",
    ]
    mod = provider_module(args.provider)
    if mod is not None:
        lines.append(f"{mod.API_KEY_ENV}: {mod.api_key_status()}")
        lines.append("NOTE: real API calls may incur charges on your account.")
    return lines


def confirm(prompt_text: str) -> bool:
    if not sys.stdin.isatty():
        return False
    return input(prompt_text).strip().lower() in ("y", "yes")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", required=True, choices=PROVIDER_NAMES)
    ap.add_argument("--model", required=True, help=f"model name; mock models: {', '.join(MOCK_MODELS)}")
    ap.add_argument("--tasks", type=Path, default=ROOT / "tasks" / "development")
    ap.add_argument("--task-ids", default=None, help="comma-separated subset of task ids, e.g. SS-DEV-001A")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    ap.add_argument("--backoff", type=float, default=None, help="base retry backoff seconds (default: 0 mock, 2 real)")
    ap.add_argument("--jitter", type=float, default=None, help="backoff jitter fraction 0..1 (default: 0 mock, 0.25 real)")
    ap.add_argument("--prompt-version", choices=sorted(PROMPT_VERSIONS), default=DEFAULT_PROMPT_VERSION,
                    help="v2 (default) = uncoached prompt; v1 = archived pilot prompt, for reproducing earlier runs")
    ap.add_argument("--structured-output", choices=["json_schema", "off"], default="json_schema")
    ap.add_argument("--temperature", type=float, default=None,
                    help="openai only; sent only if given (some models reject it)")
    ap.add_argument("--max-output-tokens", type=int, default=None,
                    help="openai: sent only if given; anthropic: max_tokens (default 16000)")
    ap.add_argument("--reasoning-effort", default=None,
                    help="openai: reasoning.effort; anthropic: output_config.effort; sent only if given")
    ap.add_argument("--timeout", type=float, default=120.0, help="per-request timeout seconds (real providers)")
    ap.add_argument("--dry-run", action="store_true", help="validate config and print the plan; zero model calls")
    ap.add_argument("--validate-api", action="store_true", help="check key/model access without generating output")
    ap.add_argument("--yes", action="store_true", help="confirm paid API calls non-interactively")
    ap.add_argument("--results-dir", type=Path, default=ROOT / "results")
    ap.add_argument("--transcripts-dir", type=Path, default=ROOT / "transcripts")
    args = ap.parse_args(argv)

    paid = args.provider != "mock"
    args.backoff = args.backoff if args.backoff is not None else (2.0 if paid else 0.0)
    args.jitter = args.jitter if args.jitter is not None else (0.25 if paid else 0.0)
    if args.trials < 1 or args.max_attempts < 1:
        print("ERROR: --trials and --max-attempts must be >= 1", file=sys.stderr)
        return 2
    if not 0 <= args.jitter <= 1 or args.backoff < 0:
        print("ERROR: --jitter must be in [0, 1] and --backoff >= 0", file=sys.stderr)
        return 2
    try:
        tasks = load_tasks(args.tasks)
    except TaskValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.task_ids:
        wanted = [t.strip() for t in args.task_ids.split(",") if t.strip()]
        unknown = sorted(set(wanted) - {t.task_id for t in tasks})
        if unknown:
            print(f"ERROR: unknown task ids: {unknown}", file=sys.stderr)
            return 2
        tasks = [t for t in tasks if t.task_id in wanted]
    problems = [p for t in tasks for p in check_against_reference(t)]
    if problems:
        print("ERROR: oracle/reference mismatch, refusing to run:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    if args.provider == "mock" and args.model not in MOCK_MODELS:
        print(f"ERROR: unknown mock model {args.model!r}", file=sys.stderr)
        return 2

    prompt_path = PROMPT_VERSIONS[args.prompt_version]
    settings = generation_settings(args)
    mod = provider_module(args.provider)
    if mod is not None:
        try:
            mod.validate_settings(settings)
        except ConfigurationError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    for line in plan_lines(args, len(tasks), settings):
        print(line)
    if args.dry_run:
        print("DRY RUN: configuration valid; no model calls were made.")
        return 0

    try:
        provider = build_provider(args.provider, args.model, tasks, settings, prompt_path)
    except ConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.validate_api:
        if not hasattr(provider, "validate_access"):
            print("Nothing to validate for this provider.")
            return 0
        try:
            model_id = provider.validate_access()
        except InfrastructureError as exc:
            print(f"API VALIDATION FAILED: {exc}", file=sys.stderr)
            return 3
        print(f"API VALIDATION OK: key accepted, model {model_id!r} is accessible. No output was generated.")
        return 0

    if provider.paid and not args.yes and not confirm("Proceed with these paid API calls? [y/N] "):
        print("Aborted: no API calls made. Re-run with --yes to confirm non-interactively.", file=sys.stderr)
        return 3

    args.results_dir.mkdir(parents=True, exist_ok=True)
    args.transcripts_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    run_id = f"run-{started.strftime('%Y%m%dT%H%M%SZ')}-{args.provider}-{uuid.uuid4().hex[:8]}"
    transcript_path = args.transcripts_dir / f"{run_id}.jsonl"

    # The exact prompts sent, once per task (identical across trials; each trial record also
    # carries their sha256 so the two can be matched).
    prompts = {}
    for t in tasks:
        p = build_prompt(t, prompt_path)
        prompts[t.task_id] = {"system": p.system, "user": p.user}
    (args.transcripts_dir / f"{run_id}.prompts.json").write_text(json.dumps(prompts, indent=2) + "\n", encoding="utf-8")

    with transcript_path.open("x", encoding="utf-8") as fh:
        def persist(rec: TrialRecord) -> None:
            fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
            fh.flush()

        run_id, records = run_eval(tasks, provider, args.trials, run_id=run_id, max_attempts=args.max_attempts,
                                   backoff_s=args.backoff, jitter=args.jitter, on_record=persist,
                                   prompt_path=prompt_path)
    finished = datetime.now(UTC)
    metrics = compute_metrics(records, tasks)

    def _sum(attr: str) -> int | None:
        vals = [getattr(r, attr) for r in records]
        return None if any(v is None for v in vals) else sum(vals)

    summary = {
        "run_id": run_id, "provider": args.provider, "model": args.model,
        "returned_models": sorted({r.returned_model for r in records if r.returned_model}),
        "prompt_version": args.prompt_version,
        "tasks_dir": str(args.tasks), "task_ids": [t.task_id for t in tasks], "trials": args.trials,
        "max_attempts": args.max_attempts, "backoff_s": args.backoff, "jitter": args.jitter,
        "generation_settings": provider.settings,
        "started_at": started.isoformat(), "finished_at": finished.isoformat(),
        "wall_clock_s": round((finished - started).total_seconds(), 3),
        "usage_reported": {"input_tokens": _sum("input_tokens"), "output_tokens": _sum("output_tokens"),
                           "total_tokens": _sum("total_tokens"),
                           "note": "provider-reported usage; None if any trial did not report it"},
        "cost": {"total_cost_usd": metrics["total_cost_usd"],
                 "note": "not computed for real providers (no hard-coded pricing); derive from usage_reported"},
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "pydantic": _pkg("pydantic"), "openai": _pkg("openai"), "anthropic": _pkg("anthropic"),
                        "git_commit": _git_commit(),
                        "prompt_template_sha256": _sha256(prompt_path),
                        "task_files_sha256": {t.task_id: _sha256(args.tasks / f"{t.task_id}.json") for t in tasks}},
        "transcripts": str(transcript_path),
        "metrics": metrics,
    }
    (args.results_dir / f"{run_id}.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    k = args.trials
    print(f"run_id: {run_id}  model: {args.model}  trials/task: {k}")
    print(f"{'task':<13} {'trial':>5} {'pass':>5} {'score':>6} {'att':>4}  failure_types")
    for r in records:
        print(f"{r.task_id:<13} {r.trial_index:>5} {r.strict_pass!s:>5} "
              f"{(r.score if r.score is not None else float('nan')):>6.2f} {r.attempt_count:>4}  {','.join(r.failure_types) or '-'}")
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
    usage = summary["usage_reported"]
    print(f"  usage_reported: input={usage['input_tokens']} output={usage['output_tokens']} total={usage['total_tokens']}")
    print(f"results: {args.results_dir / (run_id + '.json')}")
    print(f"transcripts: {transcript_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
