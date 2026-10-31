"""OpenAI provider (Responses API, official SDK).

What is sent: exactly the benchmark prompt -- `prompt.system` as `instructions` and
`prompt.user` as `input` -- plus, when structured output is on, a JSON schema that restates
the answer contract the prompt already gives (six fields, the two enums). Nothing else: no
task id, pair, variant, reference answer or oracle output ever reaches this module.

What is returned to the harness: the raw `output_text`, untouched. Parsing and grading stay in
scopeshift.grader; this module never repairs, completes or re-asks for an answer.

Retries are NOT done here (`max_retries=0` on the SDK client); the harness owns the bounded
retry loop so every attempt is counted.
"""

from __future__ import annotations

import os
from importlib import metadata

from scopeshift.errors import ConfigurationError, InfraKind, InfrastructureError, redact_secrets
from scopeshift.prompt import Prompt
from scopeshift.providers.base import Provider, ProviderResponse

API_KEY_ENV = "OPENAI_API_KEY"
STRUCTURED_OUTPUT_MODES = ("json_schema", "off")

# Mirrors schema.ModelAnswer: field set, types and enums only. Length, pattern and item-count
# limits are deliberately NOT enforced here, so violations stay visible to the benchmark parser
# instead of being prevented or silently fixed by the API (tests assert the two stay in sync).
ANSWER_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "exception_status": {"type": "string", "enum": ["ACTIVE", "TERMINATED", "NOT_APPLICABLE"]},
        "transition_event_id": {"type": ["string", "null"]},
        "governing_rule_id": {"type": "string"},
        "primary_action": {"type": "string", "enum": [
            "DOCUMENT", "MONITOR", "ESCALATE_TIER2", "OPEN_INCIDENT", "ISOLATE_HOST", "REVOKE_SESSION"]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["exception_status", "transition_event_id", "governing_rule_id", "primary_action",
                 "evidence_ids", "rationale"],
    "additionalProperties": False,
}


def sdk_version() -> str | None:
    try:
        return metadata.version("openai")
    except metadata.PackageNotFoundError:
        return None


def read_api_key() -> str:
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise ConfigurationError(
            f"{API_KEY_ENV} is not set. Export it in your shell before running "
            f"(e.g. `export {API_KEY_ENV}=...`); never put it in a file that is committed.")
    return key


def api_key_status() -> str:
    """Safe description for display: never reveals any part of the key."""
    return "set" if os.environ.get(API_KEY_ENV, "").strip() else "NOT SET"


def classify_openai_error(exc: Exception, api_key: str | None = None) -> InfrastructureError:
    """Map an SDK exception to the harness's infrastructure taxonomy (never a model failure)."""
    import openai

    msg = redact_secrets(f"{type(exc).__name__}: {exc}", api_key)
    if isinstance(exc, openai.APITimeoutError):  # subclass of APIConnectionError: check first
        return InfrastructureError(InfraKind.TIMEOUT, msg)
    if isinstance(exc, openai.APIConnectionError):
        return InfrastructureError(InfraKind.NETWORK, msg)
    if isinstance(exc, openai.RateLimitError):
        code = getattr(exc, "code", None) or ""
        kind = InfraKind.QUOTA if "insufficient_quota" in f"{code} {exc}" else InfraKind.RATE_LIMIT
        return InfrastructureError(kind, msg)
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return InfrastructureError(InfraKind.AUTH, msg)
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", 0) or 0
        return InfrastructureError(InfraKind.SERVER_ERROR if status >= 500 else InfraKind.BAD_REQUEST, msg)
    return InfrastructureError(InfraKind.TRANSPORT, msg)


class OpenAIProvider(Provider):
    name = "openai"
    paid = True

    def __init__(self, model: str, settings: dict | None = None, *, client=None) -> None:
        settings = dict(settings or {})
        mode = settings.setdefault("structured_output", "json_schema")
        if mode not in STRUCTURED_OUTPUT_MODES:
            raise ConfigurationError(f"structured_output must be one of {STRUCTURED_OUTPUT_MODES}, got {mode!r}")
        settings.setdefault("timeout_s", 120.0)
        settings["sdk"] = f"openai-python {sdk_version()}"
        settings["api"] = "responses"
        settings["store"] = False
        super().__init__(model, settings)
        self._api_key: str | None = None
        if client is None:
            from openai import OpenAI

            self._api_key = read_api_key()
            client = OpenAI(api_key=self._api_key, max_retries=0, timeout=float(settings["timeout_s"]))
        self._client = client

    def request_kwargs(self, prompt: Prompt) -> dict:
        """The exact request body. Optional generation settings are sent only when set."""
        s = self.settings
        kwargs: dict = {
            "model": self.model,
            "instructions": prompt.system,
            "input": prompt.user,
            "store": False,
        }
        if s["structured_output"] == "json_schema":
            kwargs["text"] = {"format": {"type": "json_schema", "name": "scopeshift_answer",
                                         "schema": ANSWER_JSON_SCHEMA, "strict": True}}
        if s.get("temperature") is not None:
            kwargs["temperature"] = float(s["temperature"])
        if s.get("max_output_tokens") is not None:
            kwargs["max_output_tokens"] = int(s["max_output_tokens"])
        if s.get("reasoning_effort"):
            kwargs["reasoning"] = {"effort": s["reasoning_effort"]}
        return kwargs

    def validate_access(self) -> str:
        """Cheap configuration check: retrieves the model's metadata (no generation)."""
        try:
            info = self._client.models.retrieve(self.model)
        except Exception as exc:  # noqa: BLE001 - every SDK error is classified, none swallowed
            raise classify_openai_error(exc, self._api_key) from None
        return getattr(info, "id", self.model)

    def complete(self, prompt: Prompt) -> ProviderResponse:
        try:
            resp = self._client.responses.create(**self.request_kwargs(prompt))
        except Exception as exc:  # noqa: BLE001
            raise classify_openai_error(exc, self._api_key) from None

        status = getattr(resp, "status", None)
        if status == "failed":
            err = getattr(resp, "error", None)
            raise InfrastructureError(InfraKind.SERVER_ERROR, f"response {getattr(resp, 'id', '?')} failed: {err}")

        usage = getattr(resp, "usage", None)
        refusals = [c.refusal for item in (getattr(resp, "output", None) or [])
                    for c in (getattr(item, "content", None) or []) if getattr(c, "type", None) == "refusal"]
        incomplete = getattr(resp, "incomplete_details", None)
        meta = {
            "refusal": refusals or None,
            "incomplete_details": incomplete.model_dump(mode="json") if hasattr(incomplete, "model_dump") else incomplete,
            "response_dump": redact_secrets(_dump_json(resp), self._api_key),
        }
        return ProviderResponse(
            text=getattr(resp, "output_text", None),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
            cost_usd=None,
            response_id=getattr(resp, "id", None),
            returned_model=getattr(resp, "model", None),
            status=status,
            raw_meta=meta,
        )


def _dump_json(resp) -> str:
    import json

    if hasattr(resp, "model_dump"):
        return json.dumps(resp.model_dump(mode="json"), ensure_ascii=False)
    return json.dumps({"repr": repr(resp)})
