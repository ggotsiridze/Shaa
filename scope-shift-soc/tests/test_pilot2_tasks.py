"""The harder candidate task set (tasks/pilot2): same guarantees as the development set."""

import json

import pytest
from conftest import ROOT, reference_json

from scopeshift.grader import grade, parse_response
from scopeshift.harness import run_eval
from scopeshift.loader import load_pairs, load_tasks
from scopeshift.metrics import compute_metrics
from scopeshift.oracle import State, check_against_reference, run_oracle
from scopeshift.pair_validation import validate_all_pairs
from scopeshift.prompt import PROMPT_VERSIONS, build_prompt
from scopeshift.providers.mock import MockProvider
from scopeshift.taxonomy import Failure, classify

P2 = ROOT / "tasks" / "pilot2"


@pytest.fixture(scope="module")
def p2():
    return {t.task_id: t for t in load_tasks(P2)}


GOLD = {
    "SS-DEV-003A": ("ACTIVE", None, "CHG-02", "DOCUMENT", {"E1", "E7", "E11", "E15"}),
    "SS-DEV-003B": ("TERMINATED", "E13", "CHG-05", "ESCALATE_TIER2", {"E1", "E7", "E11", "E13"}),
    "SS-DEV-004A": ("TERMINATED", "E9", "CHG-05", "ESCALATE_TIER2", {"E1", "E9"}),
    "SS-DEV-004B": ("ACTIVE", None, "CHG-02", "DOCUMENT", {"E1", "E9", "E13"}),
    "SS-DEV-005A": ("ACTIVE", None, "PT-05", "ESCALATE_TIER2", {"E1", "E12"}),
    "SS-DEV-005B": ("TERMINATED", "E12", "PT-03", "OPEN_INCIDENT", {"E1", "E12"}),
    "SS-DEV-006A": ("ACTIVE", None, "CHG-02", "DOCUMENT", {"E1", "E11"}),
    "SS-DEV-006B": ("TERMINATED", "E8", "CHG-05", "ESCALATE_TIER2", {"E1", "E8"}),
}


def test_gold_answers_and_oracle(p2):
    assert set(p2) == set(GOLD)
    for tid, (st, tr, rule, act, ev) in GOLD.items():
        r = run_oracle(p2[tid])
        assert (r.exception_status.value, r.transition_event_id, r.governing_rule_id, r.primary_action.value) == (st, tr, rule, act), tid
        assert set(r.required_evidence_ids) == ev and check_against_reference(p2[tid]) == [], tid


def test_pairs_differ_only_in_declared_fields(p2):
    reports = validate_all_pairs(load_pairs(P2), list(p2.values()))
    assert all(r.ok for r in reports), [r.problems for r in reports]


def test_reference_answers_pass_and_opposites_fail(p2):
    for tid, t in p2.items():
        assert grade(t, parse_response(reference_json(t))).strict_pass, tid
        mate = p2[tid[:-1] + ("B" if tid.endswith("A") else "A")]
        assert not grade(t, parse_response(reference_json(mate))).strict_pass, tid


def test_no_leakage_under_either_prompt(p2):
    for t in p2.values():
        for path in PROMPT_VERSIONS.values():
            p = build_prompt(t, path)
            text = p.system + p.user
            for token in ("hidden_reference", "oracle_spec", "pair_id", "DEV-PAIR", "variant", t.task_id,
                          "required_evidence_ids", "event_semantics", "TIME_MARKER", "probes"):
                assert token not in text, (t.task_id, token)


def test_v2_prompt_removes_coaching():
    v2 = PROMPT_VERSIONS["v2"].read_text()
    for coaching in ("Do not assume", "Only sources explicitly granted authority", "can begin valid and later cease",
                     "Authorization state is judged at the time of each event"):
        assert coaching not in v2


def test_backdated_revocation_not_retroactive(p2):
    r = run_oracle(p2["SS-DEV-004A"])
    assert r.event_states["E7"] == State.ACTIVE and r.event_coverage["E7"] is True  # 23:16, after the stated 23:10
    assert r.transition_event_id == "E9"
    ans = json.loads(reference_json(p2["SS-DEV-004A"]))
    ans["transition_event_id"] = "E7"  # the "effective 23:10" reading
    a = parse_response(json.dumps(ans))
    tags = classify(p2["SS-DEV-004A"], a, grade(p2["SS-DEV-004A"], a).strict_pass)
    assert Failure.WRONG_TRANSITION_EVENT in tags and Failure.EARLY_TRANSITION_ON_ACTIVE_EVENT in tags


def test_email_revocation_rejected_and_authority_confusion_detectable(p2):
    t = p2["SS-DEV-004B"]
    r = run_oracle(t)
    assert r.rejected_claim_event_ids == ["E9"] and r.exception_status.value == "ACTIVE"
    naive = run_oracle(t, enforce_source_authority=False)
    assert (naive.exception_status.value, naive.transition_event_id) == ("TERMINATED", "E9")
    wrong = parse_response(json.dumps({"exception_status": "TERMINATED", "transition_event_id": "E9", "governing_rule_id": "CHG-05",
                                       "primary_action": "ESCALATE_TIER2", "evidence_ids": ["E1", "E9"], "rationale": "x"}))
    assert Failure.SOURCE_AUTHORITY_CONFUSION in classify(t, wrong, False)


def test_timezone_window(p2):
    a, b = run_oracle(p2["SS-DEV-006A"]), run_oracle(p2["SS-DEV-006B"])
    assert a.effective_window_end.isoformat() == "2026-10-28T04:00:00+00:00"
    assert b.effective_window_end.isoformat() == "2026-10-28T04:00:00+01:00"  # == 03:00Z
    assert b.transition_event_id == "E8" and p2["SS-DEV-006B"].model_visible.timeline[7].timestamp.isoformat() == "2026-10-28T03:00:00+00:00"


def test_stop_condition_needs_permitted_telemetry(p2):
    a, b = run_oracle(p2["SS-DEV-005A"]), run_oracle(p2["SS-DEV-005B"])
    assert a.exception_status.value == "ACTIVE" and a.event_coverage["E12"] is False and a.governing_rule_id == "PT-05"
    assert b.termination_cause.value == "STOP_CONDITION" and b.transition_event_id == "E12"
    assert p2["SS-DEV-005A"].model_visible.alert == p2["SS-DEV-005B"].model_visible.alert


def test_mock_runs_on_pilot2(p2):
    tasks = list(p2.values())
    _, recs = run_eval(tasks, MockProvider.from_tasks("mock-perfect", tasks), trials=2)
    m = compute_metrics(recs, tasks)
    assert m["strict_pass_pow_2"] == 1.0 and m["pair_pass_pow_2"] == 1.0
    _, recs = run_eval(tasks, MockProvider.from_tasks("mock-missed-transition", tasks), trials=1)
    assert compute_metrics(recs, tasks)["pair_pass_pow_1"] == 0.0


def test_mock_works_with_archived_v1_prompt(p2):
    tasks = list(p2.values())
    path = PROMPT_VERSIONS["v1"]
    _, recs = run_eval(tasks, MockProvider.from_tasks("mock-perfect", tasks, prompt_path=path), trials=1, prompt_path=path)
    assert all(r.strict_pass for r in recs)
