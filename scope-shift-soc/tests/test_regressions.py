"""Regression tests for BUG_LOG.md entries."""

import json
import shutil

import pytest
from conftest import TASK_DIR, activity, answer_json, build_task, create, raw_task

import run_eval
from scopeshift.errors import TaskValidationError
from scopeshift.grader import parse_response
from scopeshift.loader import load_tasks
from scopeshift.oracle import check_against_reference, run_oracle
from scopeshift.providers.mock import MockProvider
from scopeshift.schema import Task


def test_bug001_missed_transition_mock_cites_authorizing_events(by_id):
    from scopeshift.prompt import build_prompt
    t = by_id["SS-DEV-001A"]
    p = MockProvider.from_tasks("mock-missed-transition", [t])
    ans = json.loads(p.complete(build_prompt(t)).text)
    assert ans["evidence_ids"] == ["E1", "E4", "E6"]


def test_bug002_crlf_fence_parses():
    assert parse_response("```json\r\n" + answer_json() + "\r\n```").exception_status == "ACTIVE"


def test_bug003_inverted_window_rejected():
    with pytest.raises(ValueError, match="valid_until must be after"):
        build_task([create("E1", "2026-10-30T03:00:00Z", "2026-10-30T04:00:00Z", "2026-10-30T03:00:00Z"),
                    activity("E2", "2026-10-30T03:30:00Z")])


def test_bug004_missing_details_reported_not_crash():
    raw = raw_task("SS-DEV-001A")
    del raw["model_visible"]["timeline"][0]["details"]["valid_until"]
    problems = check_against_reference(Task.model_validate(raw))
    assert len(problems) == 1 and "missing required field 'valid_until'" in problems[0]


def test_bug005_identical_prompts_rejected_by_mock(tasks):
    with pytest.raises(ValueError, match="identical prompts"):
        MockProvider.from_tasks("mock-perfect", [tasks[0], tasks[0].model_copy(update={"task_id": "SS-DEV-009A"})])


def test_bug006_stray_task_file_rejected(tmp_path):
    for f in TASK_DIR.glob("*.json"):
        shutil.copy(f, tmp_path)
    shutil.copy(TASK_DIR / "SS-DEV-001A.json", tmp_path / "ss-dev-003a.json")
    with pytest.raises(TaskValidationError, match="unexpected JSON files"):
        load_tasks(tmp_path)


def test_bug007_zero_trials_clean_error():
    assert run_eval.main(["--provider", "mock", "--model", "mock-perfect", "--tasks", str(TASK_DIR), "--trials", "0"]) == 2


def test_window_passed_without_events_counts_as_expired():
    # Documented convention: authorization existed and lapsed -> TERMINATED at first later event.
    t = build_task([create("E1", "2026-10-30T02:00:00Z", "2026-10-30T03:00:00Z", "2026-10-30T04:00:00Z"),
                    activity("E2", "2026-10-30T04:30:00Z")])
    r = run_oracle(t)
    assert (r.exception_status.value, r.transition_event_id) == ("TERMINATED", "E2")


def test_bug009_reference_status_and_transition_checked_independently():
    # Tampered reference that differs from the oracle ONLY in status/transition.
    raw = raw_task("SS-DEV-002B")
    raw["hidden_reference"]["exception_status"] = "ACTIVE"
    raw["hidden_reference"]["transition_event_id"] = None
    problems = check_against_reference(Task.model_validate(raw))
    assert len(problems) == 2
    assert any("exception_status" in p for p in problems)
    assert any("transition_event_id" in p for p in problems)


def test_bug009_reference_evidence_checked():
    raw = raw_task("SS-DEV-002B")
    raw["hidden_reference"]["required_evidence_ids"] = ["E1", "E6", "E7"]
    problems = check_against_reference(Task.model_validate(raw))
    assert len(problems) == 1 and "required_evidence_ids" in problems[0]


def test_bug010_authority_confusion_requires_citing_rejected_event(by_id):
    from scopeshift.taxonomy import Failure, classify
    t = by_id["SS-DEV-001B"]
    base = {"exception_status": "ACTIVE", "transition_event_id": None, "governing_rule_id": "CHG-02",
            "primary_action": "DOCUMENT", "rationale": "x"}
    without = parse_response(json.dumps({**base, "evidence_ids": ["E1", "E6"]}))
    with_e4 = parse_response(json.dumps({**base, "evidence_ids": ["E1", "E4", "E6"]}))
    assert Failure.SOURCE_AUTHORITY_CONFUSION not in classify(t, without, False)
    assert Failure.MISSED_TERMINATION in classify(t, without, False)
    assert Failure.SOURCE_AUTHORITY_CONFUSION in classify(t, with_e4, False)
