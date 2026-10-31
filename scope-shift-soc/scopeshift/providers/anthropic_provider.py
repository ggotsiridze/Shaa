"""Anthropic (Claude) provider (Messages API, official SDK).

What is sent: exactly the benchmark prompt -- `prompt.system` as `system` and `prompt.user` as
the single user message -- plus, when structured output is on, `output_config.format` with the
same JSON schema the OpenAI provider uses (it restates the answer contract the prompt already
gives). `thinking` is never sent (each model runs its default); `output_config.effort` only when
given. No task id, pair, variant, reference answer or oracle output ever reaches this module.

Benchmark-validity choices:
  * Server-side refusal fallbacks are deliberately NOT enabled: a fallback would let a different
    model answer silently. A refusal is recorded as the tested model's own output.
  * `base_url` is pinned to the public API, so an ambient ANTHROPIC_BASE_URL (e.g. a proxy set by
    the host environment) can never redirect requests or the key.
  * SDK retries are disabled (`max_retries=0`); the harness owns the bounded retry loop.

The raw text (all text blocks, in order) is returned untouched; parsing and grading stay in
scopeshift.grader.
"""

from __future__ import annotations

import json
import os
from importlib import metadata

from scopeshift.errors import ConfigurationError, InfraKind, InfrastructureError, redact_secrets
from scopeshift.prompt import Prompt
from scopeshift.providers.base import Provider, ProviderResponse
from scopeshift.providers.openai_provider import ANSWER_JSON_SCHEMA, STRUCTURED_OUTPUT_MODES

API_KEY_ENV = "ANTHROPIC_API_KEY"
API_BASE_URL = "https://api.anthropic.com"
DEFAULT_MAX_TOKENS = 16000  # required by the API; thinking tokens count against it


def sdk_version() -> str | None:
    try:
        return metadata.version("anthropic")
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
    return "set" if os.environ.get(API_KEY_ENV, "").strip() else "NOT SET"


def validate_settings(settings: dict) -> None:
    """Reject settings this API cannot take, before any call (and in --dry-run)."""
    if settings.get("temperature") is not None:
        raise ConfigurationError("--temperature is not supported for the anthropic provider: current Claude "
                                 "models and API versions reject sampling parameters. Use --reasoning-effort.")
    mode = settings.get("structured_output", "json_schema")
    if mode not in STRUCTURED_OUTPUT_MODES:
        raise ConfigurationError(f"structured_output must be one of {STRUCTURED_OUTPUT_MODES}, got {mode!r}")


def classify_anthropic_error(exc: Exception, api_key: str | None = None) -> InfrastructureError:
    """Map an SDK exception to the harness's infrastructure taxonomy (never a model failure)."""
    import anthropic

    msg = redact_secrets(f"{type(exc).__name__}: {exc}", api_key)
    if isinstance(exc, anthropic.APITimeoutError):  # subclass of APIConnectionError: check first
        return InfrastructureError(InfraKind.TIMEOUT, msg)
    if isinstance(exc, anthropic.APIConnectionError):
        return InfrastructureError(InfraKind.NETWORK, msg)
    if isinstance(exc, anthropic.RateLimitError):
        return InfrastructureError(InfraKind.RATE_LIMIT, msg)
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError | anthropic.CredentialsError):
        return InfrastructureError(InfraKind.AUTH, msg)
    if isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", 0) or 0
        if status == 402:  # billing_error
            return InfrastructureError(InfraKind.QUOTA, msg)
        # 500 api_error, 529 overloaded, 503/504 -> transient; 400/404/413/422 -> configuration
        return InfrastructureError(InfraKind.SERVER_ERROR if status >= 500 else InfraKind.BAD_REQUEST, msg)
    return InfrastructureError(InfraKind.TRANSPORT, msg)


class AnthropicProvider(Provider):
    name = "anthropic"
    paid = True

    def __init__(self, model: str, settings: dict | None = None, *, client=None) -> None:
        settings = dict(settings or {})
        settings.setdefault("structured_output", "json_schema")
        validate_settings(settings)
        settings.pop("temperature", None)
        if settings.get("max_output_tokens") is None:
            settings["max_output_tokens"] = DEFAULT_MAX_TOKENS
        settings.setdefault("timeout_s", 120.0)
        settings["sdk"] = f"anthropic-python {sdk_version()}"
        settings["api"] = "messages"
        settings["base_url"] = API_BASE_URL
        settings["thinking"] = "model default (not sent)"
        settings["refusal_fallbacks"] = "disabled (a fallback would let another model answer)"
        super().__init__(model, settings)
        self._api_key: str | None = None
        if client is None:
            from anthropic import Anthropic

            self._api_key = read_api_key()
            client = Anthropic(api_key=self._api_key, base_url=API_BASE_URL, max_retries=0,
                               timeout=float(settings["timeout_s"]))
        self._client = client

    def request_kwargs(self, prompt: Prompt) -> dict:
        """The exact request body. Optional settings are sent only when set."""
        s = self.settings
        kwargs: dict = {
            "model": self.model,
            "max_tokens": int(s["max_output_tokens"]),
            "system": prompt.system,
            "messages": [{"role": "user", "content": prompt.user}],
        }
        output_config: dict = {}
        if s["structured_output"] == "json_schema":
            output_config["format"] = {"type": "json_schema", "schema": ANSWER_JSON_SCHEMA}
        if s.get("reasoning_effort"):
            output_config["effort"] = s["reasoning_effort"]
        if output_config:
            kwargs["output_config"] = output_config
        return kwargs

    def validate_access(self) -> str:
        """Cheap configuration check: retrieves the model's metadata (no generation)."""
        try:
            info = self._client.models.retrieve(self.model)
        except Exception as exc:  # noqa: BLE001 - every SDK error is classified, none swallowed
            raise classify_anthropic_error(exc, self._api_key) from None
        return getattr(info, "id", self.model)

    def complete(self, prompt: Prompt) -> ProviderResponse:
        try:
            msg = self._client.messages.create(**self.request_kwargs(prompt))
        except Exception as exc:  # noqa: BLE001
            raise classify_anthropic_error(exc, self._api_key) from None

        blocks = list(getattr(msg, "content", None) or [])
        text_parts = [b.text for b in blocks if getattr(b, "type", None) == "text"]
        usage = getattr(msg, "usage", None)
        stop_details = getattr(msg, "stop_details", None)
        meta = {
            "stop_reason": getattr(msg, "stop_reason", None),
            "stop_details": stop_details.model_dump(mode="json") if hasattr(stop_details, "model_dump") else stop_details,
            "content_block_types": [getattr(b, "type", None) for b in blocks],
            "request_id": getattr(msg, "_request_id", None),
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
            "response_dump": redact_secrets(_dump_json(msg), self._api_key),
        }
        return ProviderResponse(
            # "" when the model produced no text (e.g. a refusal); None only if content is absent.
            text="".join(text_parts) if blocks else None,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            total_tokens=None,  # the Messages API reports input/output separately, no total
            cost_usd=None,
            response_id=getattr(msg, "id", None),
            returned_model=getattr(msg, "model", None),
            status=getattr(msg, "stop_reason", None),
            raw_meta=meta,
        )


def _dump_json(msg) -> str:
    if hasattr(msg, "model_dump"):
        return json.dumps(msg.model_dump(mode="json"), ensure_ascii=False)
    return json.dumps({"repr": repr(msg)})
