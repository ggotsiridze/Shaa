"""OpenAI adapter tests. The SDK boundary is faked: these tests make NO network or paid calls."""

import json
from types import SimpleNamespace

import httpx2
import openai
import pytest
from conftest import ROOT, TASK_DIR, reference_json

import run_eval
from scopeshift.errors import ConfigurationError, InfraKind, InfrastructureError
from scopeshift.harness import call_with_retries, run_trial
from scopeshift.prompt import build_prompt
from scopeshift.providers import build_provider
from scopeshift.providers import openai_provider as op
from scopeshift.schema import ModelAnswer

SECRET = "sk-proj-TESTSECRETabcdef1234567890"
_REQ = httpx2.Request("POST", "https://api.openai.com/v1/responses")


def _usage(i=1200, o=150):
    return SimpleNamespace(input_tokens=i, output_tokens=o, total_tokens=i + o)


class FakeResponse:
    def __init__(self, text, *, status="completed", usage=None, rid="resp_test123", model="gpt-test-2026-01-01",
                 output=None, error=None, incomplete=None):
        self.output_text, self.status, self.usage, self.id, self.model = text, status, usage, rid, model
        self.output, self.error, self.incomplete_details = output or [], error, incomplete

    def model_dump(self, mode="json"):
        return {"id": self.id, "model": self.model, "status": self.status, "output_text": self.output_text}


class FakeClient:
    """Stands in for openai.OpenAI. Each scripted item is a FakeResponse or an exception."""

    def __init__(self, script):
        self.script, self.calls, self.model_lookups = list(script), [], []
        self.responses = SimpleNamespace(create=self._create)
        self.models = SimpleNamespace(retrieve=self._retrieve)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _retrieve(self, model):
        self.model_lookups.append(model)
        return SimpleNamespace(id=model)


def provider(script, **settings):
    client = FakeClient(script)
    return op.OpenAIProvider("gpt-test", settings, client=client), client


def _status_error(cls, code, body=None):
    return cls("boom", response=httpx2.Response(code, request=_REQ), body=body)


# 1, 2 --------------------------------------------------------------------------------------

def test_exact_prompt_is_sent(tasks):
    t = tasks[1]
    p, client = provider([FakeResponse(reference_json(t), usage=_usage())])
    prompt = build_prompt(t)
    p.complete(prompt)
    sent = client.calls[0]
    assert sent["instructions"] == prompt.system and sent["input"] == prompt.user
    assert sent["model"] == "gpt-test" and sent["store"] is False
    assert set(sent) == {"model", "instructions", "input", "store", "text"}  # nothing else by default


def test_hidden_data_not_sent(tasks):
    for t in tasks:
        p, client = provider([FakeResponse("{}")])
        p.complete(build_prompt(t))
        blob = json.dumps(client.calls[0])
        for token in ("hidden_reference", "oracle_spec", "internal_metadata", "pair_id", "DEV-PAIR", "variant",
                      "required_evidence_ids", t.task_id, "rule_map", "event_semantics"):
            assert token not in blob, (t.task_id, token)
        ref = t.hidden_reference
        assert json.dumps(ref.model_dump(mode="json")) not in blob


# 3 -----------------------------------------------------------------------------------------

def test_model_and_settings_respected(tasks):
    p, client = provider([FakeResponse("{}")], temperature=0, max_output_tokens=900, reasoning_effort="low")
    p.complete(build_prompt(tasks[0]))
    sent = client.calls[0]
    assert sent["temperature"] == 0.0 and sent["max_output_tokens"] == 900 and sent["reasoning"] == {"effort": "low"}
    assert p.settings["api"] == "responses" and p.settings["sdk"].startswith("openai-python")


def test_structured_output_off_sends_plain_prompt(tasks):
    p, client = provider([FakeResponse("{}")], structured_output="off")
    p.complete(build_prompt(tasks[0]))
    assert "text" not in client.calls[0]


def test_structured_output_schema_matches_answer_contract():
    s = op.ANSWER_JSON_SCHEMA
    assert set(s["properties"]) == set(ModelAnswer.model_fields) == set(s["required"])
    assert s["additionalProperties"] is False
    from typing import get_args
    assert s["properties"]["exception_status"]["enum"] == list(get_args(ModelAnswer.model_fields["exception_status"].annotation))
    assert s["properties"]["primary_action"]["enum"] == list(get_args(ModelAnswer.model_fields["primary_action"].annotation))
    # limits stay with the benchmark parser so violations remain observable, not prevented
    blob = json.dumps(s)
    assert "maxItems" not in blob and "maxLength" not in blob and "pattern" not in blob


def test_invalid_structured_output_mode_rejected():
    with pytest.raises(ConfigurationError):
        op.OpenAIProvider("gpt-test", {"structured_output": "loose"}, client=FakeClient([]))


# 4, 5 --------------------------------------------------------------------------------------

def test_api_key_read_from_environment(monkeypatch):
    seen = {}

    class CapturingOpenAI:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setattr(openai, "OpenAI", CapturingOpenAI)
    p = op.OpenAIProvider("gpt-test", {})
    assert seen["api_key"] == SECRET and seen["max_retries"] == 0  # harness owns retries
    assert SECRET not in json.dumps(p.settings)
    assert op.api_key_status() == "set"


def test_missing_api_key_fails_cleanly(monkeypatch, tasks):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY is not set"):
        build_provider("openai", "gpt-test", tasks, {})
    assert run_eval.main(["--provider", "openai", "--model", "gpt-test", "--trials", "1", "--yes"]) == 2


def test_blank_api_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    with pytest.raises(ConfigurationError):
        op.read_api_key()


# 6, 7, 11, 12 ------------------------------------------------------------------------------

def test_successful_response_preserved_and_graded(tasks):
    t = tasks[1]
    raw = reference_json(t)
    p, _ = provider([FakeResponse(raw, usage=_usage(1500, 210))])
    rec = run_trial(t, p, "run-x", 0)
    assert rec.raw_response == raw  # byte-identical
    assert rec.strict_pass and rec.score == 1.0 and rec.parsed_response is not None
    assert (rec.input_tokens, rec.output_tokens, rec.total_tokens) == (1500, 210, 1710)
    assert rec.response_id == "resp_test123" and rec.returned_model == "gpt-test-2026-01-01"
    assert rec.provider_status == "completed" and rec.cost_usd is None
    assert json.loads(rec.provider_meta["response_dump"])["id"] == "resp_test123"
    assert rec.prompt_user_sha256 and rec.prompt_system_sha256


def test_malformed_output_not_repaired(tasks):
    t = tasks[0]
    raw = '{"exception_status": "ACTIVE", "transition_event_id": null'  # truncated
    p, _ = provider([FakeResponse(raw, usage=_usage())])
    rec = run_trial(t, p, "run-x", 0)
    assert rec.raw_response == raw and rec.parsed_response is None
    assert rec.failure_types == ["PARSE_FAILURE"] and rec.model_failure and not rec.infrastructure_failure


def test_wrong_but_valid_answer_kept_as_is(tasks):
    t = tasks[1]  # 001B, gold TERMINATED
    wrong = json.loads(reference_json(tasks[0]))  # 001A's answer
    p, client = provider([FakeResponse(json.dumps(wrong), usage=_usage())])
    rec = run_trial(t, p, "run-x", 0)
    assert not rec.strict_pass and rec.parsed_response["exception_status"] == "ACTIVE"
    assert len(client.calls) == 1 and rec.attempt_count == 1  # 10: not retried


def test_empty_text_with_refusal_is_model_failure(tasks):
    refusal = SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="I can't help with that.")])
    p, _ = provider([FakeResponse("", output=[refusal], usage=_usage())])
    rec = run_trial(tasks[0], p, "run-x", 0)
    assert rec.failure_types == ["PARSE_FAILURE"] and not rec.infrastructure_failure
    assert rec.provider_meta["refusal"] == ["I can't help with that."]


def test_missing_usage_recorded_as_none(tasks):
    p, _ = provider([FakeResponse(reference_json(tasks[0]), usage=None)])
    rec = run_trial(tasks[0], p, "run-x", 0)
    assert rec.input_tokens is None and rec.output_tokens is None and rec.total_tokens is None


def test_incomplete_response_kept_with_details(tasks):
    inc = SimpleNamespace(model_dump=lambda mode="json": {"reason": "max_output_tokens"})
    p, _ = provider([FakeResponse('{"exception_status": "ACT', status="incomplete", incomplete=inc, usage=_usage())])
    rec = run_trial(tasks[0], p, "run-x", 0)
    assert rec.provider_status == "incomplete" and rec.provider_meta["incomplete_details"] == {"reason": "max_output_tokens"}
    assert rec.failure_types == ["PARSE_FAILURE"]


def test_failed_response_status_is_infrastructure(tasks):
    p, _ = provider([FakeResponse("", status="failed", error={"code": "server_error"})] * 3)
    rec = run_trial(tasks[0], p, "run-x", 0, max_attempts=3)
    assert rec.infrastructure_failure and rec.infrastructure_error_kind == "SERVER_ERROR" and not rec.model_failure


# 8, 9 --------------------------------------------------------------------------------------

def test_timeout_is_infrastructure_failure(tasks):
    p, client = provider([openai.APITimeoutError(request=_REQ)] * 3)
    rec = run_trial(tasks[0], p, "run-x", 0, max_attempts=3)
    assert rec.infrastructure_failure and not rec.model_failure and rec.score is None
    assert rec.infrastructure_error_kind == "TIMEOUT" and rec.attempt_count == 3 and rec.retry_count == 2
    assert rec.input_tokens is None and rec.cost_usd is None  # unknown, not fabricated
    assert len(client.calls) == 3


@pytest.mark.parametrize("exc,kind", [
    (_status_error(openai.RateLimitError, 429), "RATE_LIMIT"),
    (_status_error(openai.InternalServerError, 503), "SERVER_ERROR"),
    (openai.APIConnectionError(request=_REQ), "NETWORK"),
])
def test_transient_errors_retry_bounded(tasks, exc, kind):
    p, client = provider([exc] * 10)
    sleeps = []
    resp, attempts, err = call_with_retries(p, build_prompt(tasks[0]), max_attempts=4, backoff_s=1.0,
                                            sleep=sleeps.append, jitter=0.25, rng=lambda: 1.0)
    assert resp is None and attempts == 4 and len(client.calls) == 4 and err.kind.value == kind
    assert sleeps == [1.25, 2.5, 5.0]  # exponential with +25% jitter at rng=1.0


def test_transient_error_then_success(tasks):
    t = tasks[0]
    p, _ = provider([_status_error(openai.RateLimitError, 429), FakeResponse(reference_json(t), usage=_usage())])
    rec = run_trial(t, p, "run-x", 0, max_attempts=3)
    assert rec.strict_pass and rec.attempt_count == 2 and rec.retry_count == 1 and not rec.infrastructure_failure


@pytest.mark.parametrize("exc,kind", [
    (_status_error(openai.AuthenticationError, 401), "AUTH"),
    (_status_error(openai.PermissionDeniedError, 403), "AUTH"),
    (_status_error(openai.BadRequestError, 400), "BAD_REQUEST"),
    (_status_error(openai.NotFoundError, 404), "BAD_REQUEST"),
    (_status_error(openai.RateLimitError, 429, body={"error": {"code": "insufficient_quota"}}), "QUOTA"),
])
def test_non_retryable_errors_stop_immediately(tasks, exc, kind):
    if kind == "QUOTA":
        exc.code = "insufficient_quota"
    p, client = provider([exc] * 3)
    rec = run_trial(tasks[0], p, "run-x", 0, max_attempts=3)
    assert rec.infrastructure_failure and rec.infrastructure_error_kind == kind
    assert len(client.calls) == 1 and not rec.model_failure


# 13 ----------------------------------------------------------------------------------------

def test_api_key_never_in_errors():
    exc = _status_error(openai.AuthenticationError, 401)
    exc.message = f"Incorrect API key provided: {SECRET}"
    err = op.classify_openai_error(Exception(f"failure with key {SECRET} Bearer {SECRET}"), SECRET)
    assert SECRET not in str(err) and "[REDACTED]" in str(err)
    err2 = InfrastructureError(InfraKind.AUTH, "Incorrect API key provided: sk-proj-abc***********xyz12")
    assert "sk-proj" not in str(err2)


def test_api_key_never_in_results_or_transcripts(monkeypatch, tmp_path, tasks):
    by = {build_prompt(t).user: t for t in tasks}

    class ScriptedOpenAI:
        def __init__(self, **kw):
            self.responses = SimpleNamespace(create=self._create)

        def _create(self, **kw):
            t = by[kw["input"]]
            if t.task_id == "SS-DEV-002B":
                raise _status_error(openai.AuthenticationError, 401)
            return FakeResponse(reference_json(t), usage=_usage())

    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setattr(openai, "OpenAI", ScriptedOpenAI)
    rc = run_eval.main(["--provider", "openai", "--model", "gpt-test", "--trials", "1", "--yes", "--backoff", "0",
                        "--results-dir", str(tmp_path / "r"), "--transcripts-dir", str(tmp_path / "t")])
    assert rc == 0
    for f in list((tmp_path / "r").iterdir()) + list((tmp_path / "t").iterdir()):
        text = f.read_text()
        assert SECRET not in text and "TESTSECRET" not in text, f.name
    summary = json.loads(next((tmp_path / "r").glob("*.json")).read_text())
    assert summary["generation_settings"]["api"] == "responses"
    assert summary["metrics"]["infrastructure_failure_rate"] == 0.25  # 1 of 4 -> AUTH, not a model failure
    assert summary["usage_reported"]["input_tokens"] is None  # one trial has unknown usage
    lines = [json.loads(x) for x in next((tmp_path / "t").glob("*.jsonl")).read_text().splitlines()]
    assert len(lines) == 4 and {r["task_id"] for r in lines} == {t.task_id for t in tasks}


# 14, 15 ------------------------------------------------------------------------------------

def test_dry_run_makes_zero_calls(monkeypatch, tmp_path, capsys):
    class ExplodingOpenAI:
        def __init__(self, **kw):
            raise AssertionError("dry run must not construct a client")

    monkeypatch.setattr(openai, "OpenAI", ExplodingOpenAI)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rc = run_eval.main(["--provider", "openai", "--model", "gpt-test", "--trials", "5", "--dry-run",
                        "--results-dir", str(tmp_path / "r"), "--transcripts-dir", str(tmp_path / "t")])
    out = capsys.readouterr().out
    assert rc == 0 and "DRY RUN" in out and "OPENAI_API_KEY: NOT SET" in out
    assert "Total model calls: 20" in out and "Tasks: 4" in out and "Trials per task: 5" in out
    assert not (tmp_path / "r").exists() and not (tmp_path / "t").exists()


def test_planned_call_count():
    assert run_eval.planned_calls(4, 5) == 20
    assert run_eval.planned_calls(1, 1) == 1


def test_task_subset_plan(capsys):
    rc = run_eval.main(["--provider", "openai", "--model", "gpt-test", "--task-ids", "SS-DEV-001A", "--trials", "1",
                        "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0 and "Tasks: 1" in out and "Total model calls: 1" in out


def test_unknown_task_id_rejected():
    assert run_eval.main(["--provider", "openai", "--model", "gpt-test", "--task-ids", "SS-DEV-999Z", "--dry-run"]) == 2


def test_paid_run_requires_confirmation(monkeypatch, tmp_path):
    calls = []

    class CountingOpenAI:
        def __init__(self, **kw):
            self.responses = SimpleNamespace(create=lambda **k: calls.append(k))

    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setattr(openai, "OpenAI", CountingOpenAI)
    rc = run_eval.main(["--provider", "openai", "--model", "gpt-test", "--trials", "1",  # no --yes, stdin not a tty
                        "--results-dir", str(tmp_path / "r"), "--transcripts-dir", str(tmp_path / "t")])
    assert rc == 3 and calls == []


def test_validate_api_generates_nothing(monkeypatch, capsys):
    client = FakeClient([])
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: client)
    rc = run_eval.main(["--provider", "openai", "--model", "gpt-test", "--validate-api"])
    assert rc == 0 and client.model_lookups == ["gpt-test"] and client.calls == []
    assert SECRET not in capsys.readouterr().out


# 16 ----------------------------------------------------------------------------------------

def test_mock_provider_unchanged_via_factory(tasks):
    from scopeshift.providers.mock import MockProvider
    p = build_provider("mock", "mock-perfect", tasks)
    assert isinstance(p, MockProvider) and not p.paid
    rec = run_trial(tasks[0], p, "run-x", 0)
    assert rec.strict_pass and rec.input_tokens == 0 and rec.cost_usd == 0.0


def test_mock_cli_needs_no_key_or_confirmation(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rc = run_eval.main(["--provider", "mock", "--model", "mock-perfect", "--tasks", str(TASK_DIR), "--trials", "1",
                        "--results-dir", str(tmp_path / "r"), "--transcripts-dir", str(tmp_path / "t")])
    assert rc == 0
    summary = json.loads(next((tmp_path / "r").glob("*.json")).read_text())
    assert summary["metrics"]["strict_pass_pow_1"] == 1.0 and summary["cost"]["total_cost_usd"] == 0.0


def test_gitignore_excludes_secrets():
    gi = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in gi and "!.env.example" in gi
    assert "OPENAI_API_KEY=" in (ROOT / ".env.example").read_text()
