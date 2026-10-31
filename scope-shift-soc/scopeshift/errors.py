"""Error types. Infrastructure errors are never scored as model failures."""

from __future__ import annotations

import re
from enum import Enum


class InfraKind(str, Enum):
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"  # HTTP 429 (transient)
    SERVER_ERROR = "SERVER_ERROR"  # HTTP 5xx, or a response the provider marks as failed
    NETWORK = "NETWORK"
    TRANSPORT = "TRANSPORT"  # SDK transport error
    AUTH = "AUTH"  # bad credentials: not retryable, still not a model failure
    QUOTA = "QUOTA"  # account out of quota/billing: retrying cannot help
    BAD_REQUEST = "BAD_REQUEST"  # 4xx config problem (unknown model, unsupported parameter, ...)


RETRYABLE = {InfraKind.TIMEOUT, InfraKind.RATE_LIMIT, InfraKind.SERVER_ERROR, InfraKind.NETWORK, InfraKind.TRANSPORT}


class InfrastructureError(Exception):
    def __init__(self, kind: InfraKind, message: str = "") -> None:
        super().__init__(f"{kind.value}: {redact_secrets(message)}")
        self.kind = kind
        self.retryable = kind in RETRYABLE


class TaskValidationError(Exception):
    """A task file is malformed or inconsistent with its oracle."""


class ConfigurationError(Exception):
    """The run is misconfigured (e.g. missing API key). Raised before any model call."""


_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{6,}"),  # OpenAI-style keys, including partially masked echoes
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.]{8,}"),
)


def redact_secrets(text: str, *extra_secrets: str | None) -> str:
    """Remove API keys from text that may end up in logs, exceptions or result files."""
    for secret in extra_secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", text)
    return text
