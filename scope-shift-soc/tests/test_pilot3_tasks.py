"""The long-timeline candidate set (tasks/pilot3) and the text-only prompt v3."""

import json

import pytest
from conftest import ROOT

from scopeshift.grader import grade, parse_response
from scopeshift.loader import load_pairs, load_tasks
from scopeshift.oracle import check_against_reference
from scopeshift.pair_validation import validate_all_pairs
from scopeshift.prompt import PROMPT_VERSIONS, build_prompt

P3 = ROOT / "tasks" / "pilot3"


@pytest.fixture(scope="module")
def p3():
    return load_tasks(P3)


def test_eight_tasks_with_long_timelines(p3):
    assert len(p3) == 8
    assert all(len(t.model_visible.timeline) >= 70 for t in p3)


def test_oracle_agrees_with_every_reference(p3):
    for t in p3:
        assert check_against_reference(t) == [], t.task_id


def test_pairs_valid(p3):
    reports = validate_all_pairs(load_pairs(P3), p3)
    assert reports and all(r.ok for r in reports), [r for r in reports if not r.ok]


def test_reference_answer_passes(p3):
    for t in p3:
        ref = t.hidden_reference
        ans = parse_response(json.dumps({
            "exception_status": ref.exception_status, "transition_event_id": ref.transition_event_id,
            "governing_rule_id": ref.governing_rule_id, "primary_action": ref.primary_action,
            "evidence_ids": ref.required_evidence_ids, "rationale": "reference"}))
        assert grade(t, ans).strict_pass, t.task_id


def test_v3_prompt_hides_details_v2_shows_them(p3):
    t = p3[0]
    assert '"details"' not in build_prompt(t, PROMPT_VERSIONS["v3"]).user
    assert '"details"' in build_prompt(t, PROMPT_VERSIONS["v2"]).user
    for e in t.model_visible.timeline:
        assert json.dumps(e.event)[1:-1] in build_prompt(t, PROMPT_VERSIONS["v3"]).user
