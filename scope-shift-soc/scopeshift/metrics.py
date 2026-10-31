"""Pilot metrics. Infrastructure failures are excluded from every model-quality denominator."""

from __future__ import annotations

from collections import defaultdict

from scopeshift.harness import TrialRecord
from scopeshift.schema import Task


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def compute_metrics(records: list[TrialRecord], tasks: list[Task]) -> dict:
    by_task = {t.task_id: t for t in tasks}
    total = len(records)
    infra = [r for r in records if r.infrastructure_failure]
    scored = [r for r in records if not r.infrastructure_failure]
    parsed = [r for r in scored if r.parse_error is None]

    def comp_acc(name: str) -> float | None:
        # parse failures count as wrong (component value 0)
        return _rate(sum(1 for r in scored if r.components and r.components[name] == 1.0), len(scored))

    def ref_status(r: TrialRecord) -> str:
        return by_task[r.task_id].hidden_reference.exception_status.value

    def got_status(r: TrialRecord) -> str | None:
        return r.parsed_response["exception_status"] if r.parsed_response else None

    ref_active = [r for r in parsed if ref_status(r) == "ACTIVE"]
    ref_term = [r for r in parsed if ref_status(r) == "TERMINATED"]
    stale_den = [r for r in ref_term if "STALE_AUTHORIZATION" in by_task[r.task_id].oracle_spec.probes]
    auth_probe = [r for r in scored if "SOURCE_AUTHORITY" in by_task[r.task_id].oracle_spec.probes]

    # pass@k: a task passes only if every trial was scored (no infra failure) and passed.
    per_task: dict[str, list[TrialRecord]] = defaultdict(list)
    for r in records:
        per_task[r.task_id].append(r)
    task_complete = {tid: all(not r.infrastructure_failure for r in rs) for tid, rs in per_task.items()}
    task_pass_at_k = {tid: task_complete[tid] and all(r.strict_pass for r in rs) for tid, rs in per_task.items()}

    pairs: dict[str, dict[str, str]] = defaultdict(dict)
    for t in tasks:
        pairs[t.internal_metadata.pair_id][t.internal_metadata.variant] = t.task_id
    pair_complete = {p: all(task_complete.get(tid, False) for tid in v.values()) for p, v in pairs.items()}
    pair_pass_at_k = {p: pair_complete[p] and all(task_pass_at_k.get(tid, False) for tid in v.values())
                      for p, v in pairs.items()}

    # pair_pass_rate: per (pair, trial_index), both variants strictly passed in that trial.
    idx = {(r.task_id, r.trial_index): r for r in records}
    trial_indices = sorted({r.trial_index for r in records})
    pair_trials = []
    for v in pairs.values():
        for i in trial_indices:
            rs = [idx.get((tid, i)) for tid in v.values()]
            if all(r is not None and not r.infrastructure_failure for r in rs):
                pair_trials.append(all(r.strict_pass for r in rs))  # type: ignore[union-attr]

    k = len(trial_indices)
    complete_tasks = [t for t, c in task_complete.items() if c]
    complete_pairs = [p for p, c in pair_complete.items() if c]
    return {
        "n_trials_total": total,
        "n_trials_scored": len(scored),
        "trials_per_task": k,
        "task_pass_rate": _rate(sum(r.strict_pass for r in scored), len(scored)),
        "pair_pass_rate": _rate(sum(pair_trials), len(pair_trials)),
        "mean_score": round(sum(r.score or 0.0 for r in scored) / len(scored), 4) if scored else None,
        "exception_state_accuracy": comp_acc("exception_status"),
        "transition_event_accuracy": comp_acc("transition_event_id"),
        "governing_rule_accuracy": comp_acc("governing_rule_id"),
        "primary_action_accuracy": comp_acc("primary_action"),
        "evidence_coverage": round(sum((r.components or {}).get("evidence_coverage", 0.0) for r in scored) / len(scored), 4) if scored else None,
        "source_authority_accuracy": _rate(
            sum(1 for r in auth_probe if r.components and r.components["exception_status"] == 1.0
                and r.components["transition_event_id"] == 1.0), len(auth_probe)),
        "premature_termination_rate": _rate(sum(got_status(r) == "TERMINATED" for r in ref_active), len(ref_active)),
        "missed_termination_rate": _rate(sum(got_status(r) == "ACTIVE" for r in ref_term), len(ref_term)),
        "stale_authorization_rate": _rate(sum(got_status(r) == "ACTIVE" for r in stale_den), len(stale_den)),
        "parse_failure_rate": _rate(sum(r.parse_error is not None for r in scored), len(scored)),
        "infrastructure_failure_rate": _rate(len(infra), total),
        f"strict_pass_at_{k}": _rate(sum(task_pass_at_k[t] for t in complete_tasks), len(complete_tasks)),
        f"pair_pass_at_{k}": _rate(sum(pair_pass_at_k[p] for p in complete_pairs), len(complete_pairs)),
        "tasks_incomplete_due_to_infra": sorted(t for t, c in task_complete.items() if not c),
        "per_task_pass_at_k": task_pass_at_k,
        "per_pair_pass_at_k": pair_pass_at_k,
        "failure_type_counts": _failure_counts(records),
        "total_cost_usd": round(sum(r.cost_usd for r in records), 6),
    }


def _failure_counts(records: list[TrialRecord]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in records:
        for f in r.failure_types:
            counts[f] += 1
    return dict(sorted(counts.items()))
