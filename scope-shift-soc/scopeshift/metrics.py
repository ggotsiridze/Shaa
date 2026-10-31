"""Pilot metrics. Infrastructure failures are excluded from every model-quality denominator.

Terminology (REVIEW-019):
  pass^k  = a task passes only if ALL k trials pass (consistency). Keys: strict_pass_pow_k,
            pair_pass_pow_k. This is the headline metric.
  pass@k  = a task passes if AT LEAST ONE of k trials passes. Reported separately as
            strict_pass_at_k_any for reference only.

Source-authority metrics (REVIEW-016) are defined on controlled pairs whose tasks carry the
SOURCE_AUTHORITY probe, and are computed per replicate index i (trial i of variant A with
trial i of variant B). They measure behavioural sensitivity to provenance under the controlled
contrast, not internal reasoning:
  source_authority_pair_pass_rate = fraction of (pair, i) where BOTH variants strictly pass.
  authority_contrast_consistency  = fraction of (pair, i) where BOTH variants get
                                    exception_status AND transition_event_id right.
"""

from __future__ import annotations

from collections import defaultdict

from scopeshift.harness import TrialRecord
from scopeshift.schema import Task

AUTHORITY_PROBE = "SOURCE_AUTHORITY"


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _state_and_transition_ok(r: TrialRecord) -> bool:
    return bool(r.components) and r.components["exception_status"] == 1.0 and r.components["transition_event_id"] == 1.0


def _pair_replicates(records: list[TrialRecord], pairs: dict[str, dict[str, str]]) -> dict[str, list[list[TrialRecord]]]:
    """pair_id -> list over trial indices of [record_A, record_B]; replicates with a missing or
    infrastructure-failed trial are dropped (not counted as model failures)."""
    idx = {(r.task_id, r.trial_index): r for r in records}
    indices = sorted({r.trial_index for r in records})
    out: dict[str, list[list[TrialRecord]]] = {}
    for pid, variants in pairs.items():
        reps = []
        for i in indices:
            rs = [idx.get((variants[v], i)) for v in sorted(variants)]
            if all(r is not None and not r.infrastructure_failure for r in rs):
                reps.append(rs)  # type: ignore[arg-type]
        out[pid] = reps
    return out


def compute_metrics(records: list[TrialRecord], tasks: list[Task]) -> dict:
    by_task = {t.task_id: t for t in tasks}
    total = len(records)
    infra = [r for r in records if r.infrastructure_failure]
    scored = [r for r in records if not r.infrastructure_failure]
    parsed = [r for r in scored if r.parse_error is None]

    def comp_acc(name: str) -> float | None:
        # parse failures count as wrong (no components)
        return _rate(sum(1 for r in scored if r.components and r.components[name] == 1.0), len(scored))

    def ref_status(r: TrialRecord) -> str:
        return by_task[r.task_id].hidden_reference.exception_status.value

    def got_status(r: TrialRecord) -> str | None:
        return r.parsed_response["exception_status"] if r.parsed_response else None

    ref_active = [r for r in parsed if ref_status(r) == "ACTIVE"]
    ref_term = [r for r in parsed if ref_status(r) == "TERMINATED"]
    stale_den = [r for r in ref_term if "STALE_AUTHORIZATION" in by_task[r.task_id].oracle_spec.probes]

    per_task: dict[str, list[TrialRecord]] = defaultdict(list)
    for r in records:
        per_task[r.task_id].append(r)
    task_complete = {tid: all(not r.infrastructure_failure for r in rs) for tid, rs in per_task.items()}
    task_pass_pow_k = {tid: task_complete[tid] and all(r.strict_pass for r in rs) for tid, rs in per_task.items()}
    task_pass_at_k_any = {tid: task_complete[tid] and any(r.strict_pass for r in rs) for tid, rs in per_task.items()}

    pairs: dict[str, dict[str, str]] = defaultdict(dict)
    for t in tasks:
        pairs[t.internal_metadata.pair_id][t.internal_metadata.variant] = t.task_id
    pair_complete = {p: all(task_complete.get(tid, False) for tid in v.values()) for p, v in pairs.items()}
    pair_pass_pow_k = {p: pair_complete[p] and all(task_pass_pow_k.get(tid, False) for tid in v.values())
                       for p, v in pairs.items()}

    reps = _pair_replicates(records, pairs)
    pair_trials = [all(r.strict_pass for r in rs) for p in reps for rs in reps[p]]
    authority_pairs = [p for p, v in pairs.items()
                       if all(AUTHORITY_PROBE in by_task[tid].oracle_spec.probes for tid in v.values())]
    auth_reps = [rs for p in authority_pairs for rs in reps[p]]

    k = len({r.trial_index for r in records})
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
        "source_authority_pair_pass_rate": _rate(sum(all(r.strict_pass for r in rs) for rs in auth_reps), len(auth_reps)),
        "authority_contrast_consistency": _rate(sum(all(_state_and_transition_ok(r) for r in rs) for rs in auth_reps), len(auth_reps)),
        "premature_termination_rate": _rate(sum(got_status(r) == "TERMINATED" for r in ref_active), len(ref_active)),
        "missed_termination_rate": _rate(sum(got_status(r) == "ACTIVE" for r in ref_term), len(ref_term)),
        "stale_authorization_rate": _rate(sum(got_status(r) == "ACTIVE" for r in stale_den), len(stale_den)),
        "parse_failure_rate": _rate(sum(r.parse_error is not None for r in scored), len(scored)),
        "infrastructure_failure_rate": _rate(len(infra), total),
        f"strict_pass_pow_{k}": _rate(sum(task_pass_pow_k[t] for t in complete_tasks), len(complete_tasks)),
        f"pair_pass_pow_{k}": _rate(sum(pair_pass_pow_k[p] for p in complete_pairs), len(complete_pairs)),
        f"strict_pass_at_{k}_any": _rate(sum(task_pass_at_k_any[t] for t in complete_tasks), len(complete_tasks)),
        "tasks_incomplete_due_to_infra": sorted(t for t, c in task_complete.items() if not c),
        "per_task_pass_pow_k": task_pass_pow_k,
        "per_pair_pass_pow_k": pair_pass_pow_k,
        "failure_type_counts": _failure_counts(records),
        # None when any trial's cost is unknown (real providers: no hard-coded pricing).
        "total_cost_usd": (None if any(r.cost_usd is None for r in records)
                           else round(sum(r.cost_usd for r in records), 6)),
    }


def _failure_counts(records: list[TrialRecord]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in records:
        for f in r.failure_types:
            counts[f] += 1
    return dict(sorted(counts.items()))
