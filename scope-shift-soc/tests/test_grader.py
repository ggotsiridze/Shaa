import json

import pytest
from conftest import reference_json

from scopeshift.grader import WEIGHTS, grade, parse_response


def g(task, **over):
    base = json.loads(reference_json(task))
    base.update(over)
    return grade(task, parse_response(json.dumps(base)))


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9
    assert WEIGHTS == {"exception_status": 0.25, "transition_event_id": 0.25, "governing_rule_id": 0.20,
                       "primary_action": 0.20, "evidence_coverage": 0.10}


def test_partial_scores(by_id):
    t = by_id["SS-DEV-001B"]
    assert g(t, primary_action="DOCUMENT").score == pytest.approx(0.8)
    assert g(t, governing_rule_id="CHG-01").score == pytest.approx(0.8)
    assert g(t, transition_event_id="E5").score == pytest.approx(0.75)
    assert g(t, evidence_ids=["E1"]).score == pytest.approx(0.9 + 0.1 / 3)  # 1 of 3 required


def test_unknown_event_id_fails_strict(by_id):
    t = by_id["SS-DEV-001B"]
    r = g(t, evidence_ids=["E1", "E6", "E99"])
    assert not r.strict_pass and r.unknown_event_ids == ["E99"] and r.components["evidence_coverage"] == 0.0
    r = g(t, transition_event_id="E42")
    assert not r.strict_pass and "E42" in r.unknown_event_ids


def test_unknown_rule_flagged(by_id):
    r = g(by_id["SS-DEV-001A"], governing_rule_id="CHG-99")
    assert r.unknown_rule_id and not r.strict_pass


def test_extra_valid_evidence_within_cap_ok(by_id):
    assert g(by_id["SS-DEV-001B"], evidence_ids=["E1", "E4", "E6"]).strict_pass


def test_grader_is_deterministic(by_id):
    t = by_id["SS-DEV-002B"]
    assert len({g(t, primary_action="MONITOR").score for _ in range(20)}) == 1
