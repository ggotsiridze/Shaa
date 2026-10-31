import json

import pytest

from scopeshift.errors import InfraKind, InfrastructureError
from scopeshift.harness import call_with_retries, run_eval, run_trial
from scopeshift.metrics import compute_metrics
from scopeshift.prompt import build_prompt
from scopeshift.providers.base import Provider, ProviderResponse
from scopeshift.providers.mock import MOCK_MODELS, MockProvider


def run(tasks, model, trials=3):
    p = MockProvider.from_tasks(model, tasks)
    _, recs = run_eval(tasks, p, trials=trials)
    return p, recs, compute_metrics(recs, tasks)


def by_task(recs):
    out = {}
    for r in recs:
        out.setdefault(r.task_id, []).append(r)
    return out


def test_mock_perfect(tasks):
    _, recs, m = run(tasks, "mock-perfect")
    assert all(r.strict_pass and r.score == 1.0 for r in recs)
    assert m["task_pass_rate"] == 1.0 and m["strict_pass_pow_3"] == 1.0 and m["pair_pass_pow_3"] == 1.0
    for k in ("exception_state_accuracy", "transition_event_accuracy", "governing_rule_accuracy",
              "primary_action_accuracy", "evidence_coverage", "source_authority_pair_pass_rate",
              "authority_contrast_consistency"):
        assert m[k] == 1.0, k
    assert m["parse_failure_rate"] == 0.0 and m["infrastructure_failure_rate"] == 0.0
    assert "source_authority_accuracy" not in m and "strict_pass_at_3" not in m  # REVIEW-016/019


def test_mock_wrong_action(tasks):
    _, recs, m = run(tasks, "mock-wrong-action")
    assert not any(r.strict_pass for r in recs)
    assert m["primary_action_accuracy"] == 0.0 and m["exception_state_accuracy"] == 1.0
    assert m["transition_event_accuracy"] == 1.0 and all(r.score == pytest.approx(0.8) for r in recs)
    assert m["authority_contrast_consistency"] == 1.0 and m["source_authority_pair_pass_rate"] == 0.0
    bt = by_task(recs)
    assert all(r.failure_types == ["WRONG_RESPONSE_AFTER_TRANSITION"] for r in bt["SS-DEV-001B"] + bt["SS-DEV-002B"])


def test_mock_missed_transition(tasks):
    _, recs, m = run(tasks, "mock-missed-transition")
    bt = by_task(recs)
    assert all(r.strict_pass for r in bt["SS-DEV-001A"] + bt["SS-DEV-002A"])  # constant ACTIVE fits A
    for r in bt["SS-DEV-001B"] + bt["SS-DEV-002B"]:
        assert not r.strict_pass and "MISSED_TERMINATION" in r.failure_types
    assert set(bt["SS-DEV-001B"][0].failure_types) == {"MISSED_TERMINATION", "STALE_AUTHORIZATION"}
    assert bt["SS-DEV-002B"][0].failure_types == ["MISSED_TERMINATION"]
    assert m["missed_termination_rate"] == 1.0 and m["pair_pass_pow_3"] == 0.0 and m["strict_pass_pow_3"] == 0.5
    assert m["source_authority_pair_pass_rate"] == 0.0 and m["authority_contrast_consistency"] == 0.0


def test_mock_premature_transition(tasks):
    _, recs, m = run(tasks, "mock-premature-transition")
    bt = by_task(recs)
    for r in bt["SS-DEV-001A"] + bt["SS-DEV-002A"]:
        assert r.failure_types == ["PREMATURE_TERMINATION"]
    # the alerted event (E7) is never the true transition (E6) in the revised pilot
    for r in bt["SS-DEV-001B"] + bt["SS-DEV-002B"]:
        assert r.failure_types == ["WRONG_TRANSITION_EVENT"]
    assert m["premature_termination_rate"] == 1.0 and m["task_pass_rate"] == 0.0 and m["pair_pass_pow_3"] == 0.0


def test_mock_source_authority_confusion(tasks):
    _, recs, m = run(tasks, "mock-source-authority-confusion")
    bt = by_task(recs)
    for r in bt["SS-DEV-001B"]:
        assert not r.strict_pass
        assert set(r.failure_types) == {"MISSED_TERMINATION", "STALE_AUTHORIZATION"}  # REVIEW-021
    for tid in ("SS-DEV-001A", "SS-DEV-002A", "SS-DEV-002B"):
        assert all(r.strict_pass for r in bt[tid]), tid
    assert m["source_authority_pair_pass_rate"] == 0.0 and m["authority_contrast_consistency"] == 0.0
    assert m["stale_authorization_rate"] == 1.0 and m["task_pass_rate"] == 0.75


def test_mock_malformed_and_empty_are_parse_failures(tasks):
    for model in ("mock-malformed", "mock-empty"):
        _, recs, m = run(tasks, model)
        assert all(r.failure_types == ["PARSE_FAILURE"] and r.model_failure and not r.infrastructure_failure for r in recs)
        assert all(r.attempt_count == 1 for r in recs)  # completed responses are never retried
        assert m["parse_failure_rate"] == 1.0 and m["infrastructure_failure_rate"] == 0.0


def test_timeout_is_infrastructure_failure(tasks):
    p, recs, m = run(tasks, "mock-timeout")
    assert all(r.infrastructure_failure and not r.model_failure for r in recs)
    assert all(r.failure_types == ["INFRASTRUCTURE_FAILURE"] and r.score is None for r in recs)
    assert all(r.attempt_count == 3 for r in recs) and p.calls == 36  # bounded: 3 attempts x 12 trials
    assert m["infrastructure_failure_rate"] == 1.0
    assert m["task_pass_rate"] is None and m["parse_failure_rate"] is None  # no scored trials, not 0%
    assert m["strict_pass_pow_3"] is None and len(m["tasks_incomplete_due_to_infra"]) == 4
    assert m["source_authority_pair_pass_rate"] is None and m["authority_contrast_consistency"] is None


def test_flaky_retries_then_succeeds(tasks):
    _, recs, m = run(tasks, "mock-flaky", trials=2)
    first = [r for r in recs if r.trial_index == 0]
    second = [r for r in recs if r.trial_index == 1]
    assert all(r.attempt_count == 2 and r.strict_pass for r in first)
    assert all(r.attempt_count == 1 and r.strict_pass for r in second)
    assert m["infrastructure_failure_rate"] == 0.0


class Scripted(Provider):
    name = "scripted"

    def __init__(self, script):
        super().__init__("scripted")
        self.script, self.calls = list(script), 0

    def complete(self, prompt):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return ProviderResponse(text=item)


@pytest.mark.parametrize("kind", [InfraKind.RATE_LIMIT, InfraKind.SERVER_ERROR, InfraKind.NETWORK, InfraKind.TRANSPORT])
def test_retryable_errors_retried_and_bounded(tasks, kind):
    p = Scripted([InfrastructureError(kind)] * 10)
    resp, attempts, err = call_with_retries(p, build_prompt(tasks[0]), max_attempts=4, sleep=lambda s: None)
    assert resp is None and attempts == 4 and p.calls == 4 and err.kind == kind


def test_auth_error_not_retried_but_still_infra(tasks):
    p = Scripted([InfrastructureError(InfraKind.AUTH)])
    rec = run_trial(tasks[0], p, "run-x", 0)
    assert rec.infrastructure_failure and rec.attempt_count == 1 and not rec.model_failure


def test_wrong_answer_not_retried(tasks):
    p = Scripted([json.dumps({"exception_status": "TERMINATED"}), "should never be used"])
    rec = run_trial(tasks[0], p, "run-x", 0)
    assert p.calls == 1 and rec.failure_types == ["PARSE_FAILURE"]


def test_backoff_is_exponential(tasks):
    sleeps = []
    p = Scripted([InfrastructureError(InfraKind.RATE_LIMIT)] * 3 + ["{}"])
    call_with_retries(p, build_prompt(tasks[0]), max_attempts=4, backoff_s=2.0, sleep=sleeps.append)
    assert sleeps == [2.0, 4.0, 8.0]


def test_unknown_mock_model_rejected(tasks):
    with pytest.raises(ValueError):
        MockProvider.from_tasks("mock-genius", tasks)


def test_all_required_mocks_exist():
    for m in ("mock-perfect", "mock-wrong-action", "mock-missed-transition", "mock-premature-transition",
              "mock-source-authority-confusion", "mock-malformed", "mock-empty", "mock-timeout"):
        assert m in MOCK_MODELS


def test_record_fields_complete(tasks):
    _, recs, _ = run(tasks, "mock-perfect", trials=1)
    d = recs[0].to_dict()
    for k in ("run_id", "trial_id", "task_id", "model_id", "provider", "settings", "timestamp", "raw_response",
              "parsed_response", "parse_error", "score", "strict_pass", "failure_types", "latency_s",
              "attempt_count", "infrastructure_failure", "model_failure"):
        assert k in d


def test_partial_infra_excluded_from_denominators(tasks):
    from scopeshift.harness import TrialRecord
    recs = []
    _, ok, _ = run(tasks, "mock-perfect", trials=1)
    recs.extend(ok)
    bad = TrialRecord(**{**ok[0].to_dict(), "trial_id": "x", "trial_index": 1, "infrastructure_failure": True,
                         "strict_pass": False, "score": None, "components": None, "failure_types": ["INFRASTRUCTURE_FAILURE"]})
    recs.append(bad)
    m = compute_metrics(recs, tasks)
    assert m["task_pass_rate"] == 1.0 and m["infrastructure_failure_rate"] == pytest.approx(0.2)
    assert ok[0].task_id in m["tasks_incomplete_due_to_infra"]
