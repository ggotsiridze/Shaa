"""Anthropic adapter tests: the REAL anthropic SDK over an in-memory HTTP transport.
No network access, no real key, no cost."""

import json

import anthropic
import httpx2
import pytest
from conftest import reference_json

import run_eval
from scopeshift.errors import ConfigurationError
from scopeshift.harness import run_trial
from scopeshift.prompt import build_prompt
from scopeshift.providers import anthropic_provider as ap
from scopeshift.providers import build_provider
from scopeshift.providers.openai_provider import ANSWER_JSON_SCHEMA

FAKE_KEY = "sk-ant-api03-WIRETESTSECRET000000000000"


def _message(text=None, *, stop_reason="end_turn", content=None, stop_details=None, usage=(2400, 310)):
    if content is None:
        content = [{"type": "text", "text": text}]
    body = {"id": "msg_wire_1", "type": "message", "role": "assistant", "model": "claude-test-model",
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": usage[0], "output_tokens": usage[1],
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}
    if stop_details is not None:
        body["stop_details"] = stop_details
    return body


def _provider(handler, **settings):
    client = anthropic.Anthropic(api_key=FAKE_KEY, base_url=ap.API_BASE_URL, max_retries=0,
                                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    return ap.AnthropicProvider("claude-test", settings, client=client)


def _ok(text, seen=None, **kw):
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx2.Response(200, json=_message(text, **kw), headers={"request-id": "req_wire_1"})
    return handler


def _err(code, etype, calls):
    def handler(request):
        calls.append(1)
        return httpx2.Response(code, json={"type": "error", "error": {"type": etype, "message": f"boom {FAKE_KEY}"}})
    return handler


# exact prompt / hidden data -----------------------------------------------------------------

def test_wire_request_is_exactly_the_benchmark_prompt(tasks):
    t, seen = tasks[3], []
    rec = run_trial(t, _provider(_ok(reference_json(t), seen)), "run-a", 0)
    req = seen[0]
    body = json.loads(req.content)
    prompt = build_prompt(t)
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert body["system"] == prompt.system
    assert body["messages"] == [{"role": "user", "content": prompt.user}]
    assert body["model"] == "claude-test" and body["max_tokens"] == ap.DEFAULT_MAX_TOKENS
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": ANSWER_JSON_SCHEMA}}
    assert set(body) == {"model", "max_tokens", "system", "messages", "output_config"}
    for forbidden in ("thinking", "temperature", "fallbacks", "tools"):
        assert forbidden not in body
    wire = req.content.decode()
    for token in ("hidden_reference", "oracle_spec", "pair_id", "DEV-PAIR", "variant", t.task_id):
        assert token not in wire
    assert FAKE_KEY not in wire  # key only in a header
    assert rec.strict_pass and rec.raw_response == reference_json(t)


def test_hidden_data_never_sent_for_any_task(tasks):
    for t in tasks:
        seen = []
        run_trial(t, _provider(_ok("{}", seen)), "run-a", 0)
        wire = seen[0].content.decode()
        assert json.dumps(t.hidden_reference.model_dump(mode="json")) not in wire
        assert "required_evidence_ids" not in wire and "event_semantics" not in wire


def test_settings_respected(tasks):
    seen = []
    run_trial(tasks[0], _provider(_ok("{}", seen), reasoning_effort="high", max_output_tokens=9000), "run-a", 0)
    body = json.loads(seen[0].content)
    assert body["max_tokens"] == 9000 and body["output_config"]["effort"] == "high"


def test_structured_output_off(tasks):
    seen = []
    run_trial(tasks[0], _provider(_ok("{}", seen), structured_output="off"), "run-a", 0)
    assert "output_config" not in json.loads(seen[0].content)


def test_temperature_rejected_before_any_call(tasks):
    with pytest.raises(ConfigurationError, match="temperature"):
        ap.AnthropicProvider("claude-test", {"temperature": 0}, client=object())
    assert run_eval.main(["--provider", "anthropic", "--model", "claude-test", "--temperature", "0", "--dry-run"]) == 2


# raw output, usage, model failures -----------------------------------------------------------

def test_raw_output_usage_and_ids_preserved(tasks):
    t = tasks[1]
    rec = run_trial(t, _provider(_ok(reference_json(t))), "run-a", 0)
    assert rec.raw_response == reference_json(t) and rec.strict_pass
    assert (rec.input_tokens, rec.output_tokens, rec.total_tokens) == (2400, 310, None)
    assert rec.response_id == "msg_wire_1" and rec.returned_model == "claude-test-model"
    assert rec.provider_status == "end_turn" and rec.provider_meta["request_id"] == "req_wire_1"
    assert rec.cost_usd is None and json.loads(rec.provider_meta["response_dump"])["id"] == "msg_wire_1"


def test_thinking_blocks_excluded_from_raw_text(tasks):
    t = tasks[0]
    content = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": reference_json(t)}]
    rec = run_trial(t, _provider(_ok(None, content=content)), "run-a", 0)
    assert rec.raw_response == reference_json(t) and rec.strict_pass
    assert rec.provider_meta["content_block_types"] == ["thinking", "text"]


def test_malformed_output_not_repaired(tasks):
    raw = 'Here is the JSON: {"exception_status": "ACTIVE"'
    rec = run_trial(tasks[0], _provider(_ok(raw)), "run-a", 0)
    assert rec.raw_response == raw and rec.failure_types == ["PARSE_FAILURE"] and not rec.infrastructure_failure


def test_wrong_answer_graded_not_retried(tasks):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx2.Response(200, json=_message(reference_json(tasks[0])))  # 001A's answer

    rec = run_trial(tasks[1], _provider(handler), "run-a", 0, max_attempts=3)  # graded against 001B
    assert len(calls) == 1 and not rec.strict_pass and "MISSED_TERMINATION" in rec.failure_types


def test_refusal_is_model_output_with_details(tasks):
    details = {"type": "refusal", "category": "cyber", "explanation": "declined"}
    rec = run_trial(tasks[0], _provider(_ok(None, content=[], stop_reason="refusal", stop_details=details)),
                    "run-a", 0, max_attempts=3)
    assert rec.attempt_count == 1 and not rec.infrastructure_failure
    assert rec.failure_types == ["PARSE_FAILURE"] and rec.provider_status == "refusal"
    assert rec.provider_meta["stop_details"]["category"] == "cyber"


def test_max_tokens_truncation_is_model_output(tasks):
    rec = run_trial(tasks[0], _provider(_ok('{"exception_status": "ACT', stop_reason="max_tokens")), "run-a", 0)
    assert rec.provider_status == "max_tokens" and rec.failure_types == ["PARSE_FAILURE"]


# infrastructure errors -------------------------------------------------------------------------

def test_timeout_is_infrastructure(tasks):
    def handler(request):
        raise httpx2.ReadTimeout("simulated", request=request)

    rec = run_trial(tasks[0], _provider(handler), "run-a", 0, max_attempts=2)
    assert rec.infrastructure_failure and rec.infrastructure_error_kind == "TIMEOUT" and rec.attempt_count == 2
    assert not rec.model_failure and rec.input_tokens is None


@pytest.mark.parametrize("code,etype,kind,attempts", [
    (429, "rate_limit_error", "RATE_LIMIT", 3), (500, "api_error", "SERVER_ERROR", 3),
    (529, "overloaded_error", "SERVER_ERROR", 3), (401, "authentication_error", "AUTH", 1),
    (403, "permission_error", "AUTH", 1), (402, "billing_error", "QUOTA", 1),
    (400, "invalid_request_error", "BAD_REQUEST", 1), (404, "not_found_error", "BAD_REQUEST", 1),
    (413, "request_too_large", "BAD_REQUEST", 1),
])
def test_http_errors_classified_and_bounded(tasks, code, etype, kind, attempts):
    calls = []
    rec = run_trial(tasks[0], _provider(_err(code, etype, calls)), "run-a", 0, max_attempts=3)
    assert rec.infrastructure_error_kind == kind and len(calls) == attempts
    assert rec.infrastructure_failure and not rec.model_failure
    assert FAKE_KEY not in json.dumps(rec.to_dict())


def test_transient_then_success(tasks):
    t, calls = tasks[0], []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}})
        return httpx2.Response(200, json=_message(reference_json(t)))

    rec = run_trial(t, _provider(handler), "run-a", 0, max_attempts=3)
    assert rec.strict_pass and rec.attempt_count == 2 and rec.retry_count == 1


# credentials, base URL, CLI ----------------------------------------------------------------

def test_key_from_env_and_base_url_pinned(monkeypatch):
    seen = {}

    class Capturing:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://internal-proxy.invalid")
    monkeypatch.setattr(anthropic, "Anthropic", Capturing)
    p = ap.AnthropicProvider("claude-test", {})
    assert seen["api_key"] == FAKE_KEY and seen["max_retries"] == 0
    assert seen["base_url"] == "https://api.anthropic.com"
    assert FAKE_KEY not in json.dumps(p.settings)


def test_sdk_honours_explicit_base_url_over_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://internal-proxy.invalid")
    c = anthropic.Anthropic(api_key=FAKE_KEY, base_url=ap.API_BASE_URL, max_retries=0)
    assert str(c.base_url).rstrip("/") == "https://api.anthropic.com"


def test_missing_key_fails_cleanly(monkeypatch, tasks):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="ANTHROPIC_API_KEY is not set"):
        build_provider("anthropic", "claude-test", tasks, {})
    assert run_eval.main(["--provider", "anthropic", "--model", "claude-test", "--trials", "1", "--yes"]) == 2


def test_dry_run_zero_calls(monkeypatch, tmp_path, capsys):
    class Exploding:
        def __init__(self, **kw):
            raise AssertionError("dry run must not construct a client")

    monkeypatch.setattr(anthropic, "Anthropic", Exploding)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = run_eval.main(["--provider", "anthropic", "--model", "claude-test", "--trials", "5", "--dry-run",
                        "--results-dir", str(tmp_path / "r"), "--transcripts-dir", str(tmp_path / "t")])
    out = capsys.readouterr().out
    assert rc == 0 and "Total model calls: 20" in out and "ANTHROPIC_API_KEY: NOT SET" in out
    assert not (tmp_path / "r").exists()


def test_paid_run_requires_confirmation(monkeypatch, tmp_path):
    made = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: made.append(kw))
    rc = run_eval.main(["--provider", "anthropic", "--model", "claude-test", "--trials", "1",
                        "--results-dir", str(tmp_path / "r"), "--transcripts-dir", str(tmp_path / "t")])
    assert rc == 3 and not (tmp_path / "t").exists()


def test_full_cli_run_no_key_leak(monkeypatch, tmp_path, tasks):
    by_user = {build_prompt(t).user: t for t in tasks}

    def handler(request):
        t = by_user[json.loads(request.content)["messages"][0]["content"]]
        return httpx2.Response(200, json=_message(reference_json(t)))

    real = anthropic.Anthropic

    def factory(**kw):
        return real(**kw, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))

    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setattr(anthropic, "Anthropic", factory)
    rc = run_eval.main(["--provider", "anthropic", "--model", "claude-test", "--trials", "2", "--yes",
                        "--results-dir", str(tmp_path / "r"), "--transcripts-dir", str(tmp_path / "t")])
    assert rc == 0
    for f in list((tmp_path / "r").iterdir()) + list((tmp_path / "t").iterdir()):
        assert FAKE_KEY not in f.read_text() and "WIRETESTSECRET" not in f.read_text()
    summary = json.loads(next((tmp_path / "r").glob("*.json")).read_text())
    assert summary["metrics"]["strict_pass_pow_2"] == 1.0
    assert summary["usage_reported"]["input_tokens"] == 8 * 2400
    assert summary["generation_settings"]["refusal_fallbacks"].startswith("disabled")
    assert summary["environment"]["anthropic"] == ap.sdk_version()


def test_validate_api_generates_nothing(monkeypatch, capsys):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx2.Response(200, json={"id": "claude-test", "type": "model", "display_name": "Claude Test",
                                          "created_at": "2026-01-01T00:00:00Z"})

    real = anthropic.Anthropic
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setattr(anthropic, "Anthropic",
                        lambda **kw: real(**kw, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))))
    rc = run_eval.main(["--provider", "anthropic", "--model", "claude-test", "--validate-api"])
    assert rc == 0 and paths == ["/v1/models/claude-test"]
    assert FAKE_KEY not in capsys.readouterr().out
