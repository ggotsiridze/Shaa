"""Wire-level tests: the REAL openai SDK, with an in-memory HTTP transport instead of the network.

These prove that the request the SDK actually serialises contains exactly the benchmark prompt,
and that real SDK response/exception objects flow through the adapter, parser and grader.
No network access, no real key, no cost.
"""

import json

import httpx2
import openai
import pytest
from conftest import reference_json

from scopeshift.harness import run_trial
from scopeshift.prompt import build_prompt
from scopeshift.providers.openai_provider import ANSWER_JSON_SCHEMA, OpenAIProvider

FAKE_KEY = "sk-wiretest-0000000000000000"


def _response_json(text: str, *, status: str = "completed") -> dict:
    return {
        "id": "resp_wire_1", "object": "response", "created_at": 1767225600, "status": status,
        "model": "gpt-wire-2026-01-01", "error": None, "incomplete_details": None, "instructions": None,
        "metadata": {}, "parallel_tool_calls": True, "temperature": 1.0, "top_p": 1.0, "tool_choice": "auto",
        "tools": [],
        "output": [{"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        "usage": {"input_tokens": 2100, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 180,
                  "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 2280},
    }


def _provider(handler, **settings):
    client = openai.OpenAI(api_key=FAKE_KEY, max_retries=0,
                           http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    return OpenAIProvider("gpt-wire", settings, client=client)


def test_wire_request_body_is_exactly_the_benchmark_prompt(tasks):
    seen = []
    t = tasks[2]

    def handler(request):
        seen.append(request)
        return httpx2.Response(200, json=_response_json(reference_json(t)))

    rec = run_trial(t, _provider(handler), "run-wire", 0)
    req = seen[0]
    body = json.loads(req.content)
    prompt = build_prompt(t)
    assert req.method == "POST" and req.url.path.endswith("/responses")
    assert body["instructions"] == prompt.system and body["input"] == prompt.user
    assert body["model"] == "gpt-wire" and body["store"] is False
    assert body["text"]["format"]["schema"] == ANSWER_JSON_SCHEMA and body["text"]["format"]["strict"] is True
    assert set(body) == {"model", "instructions", "input", "store", "text"}
    wire = req.content.decode()
    for token in ("hidden_reference", "oracle_spec", "pair_id", "DEV-PAIR", "variant", t.task_id):
        assert token not in wire
    # key travels only in the Authorization header, never in the body
    assert FAKE_KEY not in wire and req.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert rec.strict_pass and rec.raw_response == reference_json(t)
    assert (rec.input_tokens, rec.output_tokens, rec.total_tokens) == (2100, 180, 2280)
    assert rec.returned_model == "gpt-wire-2026-01-01" and rec.response_id == "resp_wire_1"
    assert FAKE_KEY not in json.dumps(rec.to_dict())


def test_wire_wrong_answer_is_graded_not_retried(tasks):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx2.Response(200, json=_response_json(reference_json(tasks[0])))  # 001A answer

    rec = run_trial(tasks[1], _provider(handler), "run-wire", 0, max_attempts=3)  # graded against 001B
    assert len(calls) == 1 and not rec.strict_pass and not rec.infrastructure_failure
    assert "MISSED_TERMINATION" in rec.failure_types


def test_wire_timeout_is_infrastructure(tasks):
    def handler(request):
        raise httpx2.ReadTimeout("simulated", request=request)

    rec = run_trial(tasks[0], _provider(handler), "run-wire", 0, max_attempts=2)
    assert rec.infrastructure_failure and rec.infrastructure_error_kind == "TIMEOUT" and rec.attempt_count == 2


@pytest.mark.parametrize("code,kind,attempts", [(429, "RATE_LIMIT", 3), (500, "SERVER_ERROR", 3),
                                                 (401, "AUTH", 1), (400, "BAD_REQUEST", 1)])
def test_wire_http_errors_classified(tasks, code, kind, attempts):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx2.Response(code, json={"error": {"message": f"Incorrect API key provided: {FAKE_KEY}",
                                                     "type": "x", "code": None}})

    rec = run_trial(tasks[0], _provider(handler), "run-wire", 0, max_attempts=3)
    assert rec.infrastructure_error_kind == kind and len(calls) == attempts and not rec.model_failure
    assert FAKE_KEY not in json.dumps(rec.to_dict())


def test_wire_insufficient_quota_not_retried(tasks):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx2.Response(429, json={"error": {"message": "You exceeded your current quota",
                                                    "type": "insufficient_quota", "code": "insufficient_quota"}})

    rec = run_trial(tasks[0], _provider(handler), "run-wire", 0, max_attempts=3)
    assert rec.infrastructure_error_kind == "QUOTA" and len(calls) == 1


def test_wire_malformed_text_is_parse_failure(tasks):
    def handler(request):
        return httpx2.Response(200, json=_response_json("Sure! Here is my analysis: ACTIVE"))

    rec = run_trial(tasks[0], _provider(handler), "run-wire", 0)
    assert rec.raw_response == "Sure! Here is my analysis: ACTIVE"
    assert rec.failure_types == ["PARSE_FAILURE"] and not rec.infrastructure_failure
