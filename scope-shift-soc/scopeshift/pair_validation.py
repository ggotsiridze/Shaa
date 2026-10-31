"""Controlled-pair integrity: model-visible differences must equal the declared set exactly."""

from __future__ import annotations

from dataclasses import dataclass

from scopeshift.schema import Task


def flatten_visible(task: Task) -> dict[str, object]:
    """Flatten model_visible into path -> leaf value. Timeline entries are keyed by event_id,
    so an inserted or reordered event shows up as a difference instead of shifting indices."""
    visible = task.model_visible.model_dump(mode="json")
    out: dict[str, object] = {}

    def walk(prefix: str, value: object) -> None:
        if isinstance(value, dict):
            if not value:
                out[prefix] = {}
            for k, v in value.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        elif isinstance(value, list):
            if prefix == "timeline":
                out["timeline.__order__"] = [e["event_id"] for e in value]
                for e in value:
                    walk(f"timeline[{e['event_id']}]", e)
            elif prefix == "organization_policy.rules":
                out["organization_policy.rules.__order__"] = [r["id"] for r in value]
                for r in value:
                    walk(f"organization_policy.rules[{r['id']}]", r)
            else:
                for i, v in enumerate(value):
                    walk(f"{prefix}[{i}]", v)
        else:
            out[prefix] = value

    walk("", visible)
    return out


def diff_visible(a: Task, b: Task) -> dict[str, tuple[object, object]]:
    fa, fb = flatten_visible(a), flatten_visible(b)
    _missing = "<absent>"
    return {
        k: (fa.get(k, _missing), fb.get(k, _missing))
        for k in sorted(set(fa) | set(fb))
        if fa.get(k, _missing) != fb.get(k, _missing)
    }


@dataclass
class PairReport:
    pair_id: str
    ok: bool
    problems: list[str]
    visible_diff: dict[str, tuple[object, object]]
    hidden_diff: dict[str, tuple[object, object]]


def validate_pair(pair_id: str, spec: dict, tasks_by_id: dict[str, Task]) -> PairReport:
    problems: list[str] = []
    ids = spec.get("variants", {})
    if set(ids) != {"A", "B"}:
        return PairReport(pair_id, False, [f"variants must be exactly A and B, got {sorted(ids)}"], {}, {})
    missing = [t for t in ids.values() if t not in tasks_by_id]
    if missing:
        return PairReport(pair_id, False, [f"missing task files: {missing}"], {}, {})
    a, b = tasks_by_id[ids["A"]], tasks_by_id[ids["B"]]
    for variant, task in (("A", a), ("B", b)):
        md = task.internal_metadata
        if md.pair_id != pair_id or md.variant != variant:
            problems.append(f"{task.task_id}: metadata says {md.pair_id}/{md.variant}, manifest says {pair_id}/{variant}")

    vdiff = diff_visible(a, b)
    expected = set(spec.get("expected_model_visible_differences", []))
    if not expected:
        problems.append("no expected differences declared")
    unexpected = sorted(set(vdiff) - expected)
    absent = sorted(expected - set(vdiff))
    if unexpected:
        problems.append(f"unexpected model-visible differences: {unexpected}")
    if absent:
        problems.append(f"declared differences not present: {absent}")

    ra, rb = a.hidden_reference, b.hidden_reference
    if ra.exception_status == rb.exception_status and ra.transition_event_id == rb.transition_event_id:
        problems.append("variants do not differ in gold state/transition; pair is not a contrast")

    ha = a.model_dump(mode="json", include={"hidden_reference", "oracle_spec"})
    hb = b.model_dump(mode="json", include={"hidden_reference", "oracle_spec"})
    hdiff = {k: (ha[k], hb[k]) for k in ha if ha[k] != hb[k]}
    return PairReport(pair_id, not problems, problems, vdiff, hdiff)


def validate_all_pairs(pairs: dict[str, dict], tasks: list[Task]) -> list[PairReport]:
    by_id = {t.task_id: t for t in tasks}
    reports = [validate_pair(pid, spec, by_id) for pid, spec in sorted(pairs.items())]
    paired = {tid for spec in pairs.values() for tid in spec.get("variants", {}).values()}
    orphans = sorted(set(by_id) - paired)
    if orphans:
        reports.append(PairReport("<orphans>", False, [f"tasks not in any pair: {orphans}"], {}, {}))
    return reports
