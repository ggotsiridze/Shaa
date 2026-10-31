"""Error types. Infrastructure errors are never scored as model failures."""

from __future__ import annotations

from enum import Enum


class InfraKind(str, Enum):
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"  # HTTP 429
    SERVER_ERROR = "SERVER_ERROR"  # HTTP 5xx
    NETWORK = "NETWORK"
    TRANSPORT = "TRANSPORT"  # SDK transport error
    AUTH = "AUTH"  # bad credentials: not retryable, still not a model failure


RETRYABLE = {InfraKind.TIMEOUT, InfraKind.RATE_LIMIT, InfraKind.SERVER_ERROR, InfraKind.NETWORK, InfraKind.TRANSPORT}


class InfrastructureError(Exception):
    def __init__(self, kind: InfraKind, message: str = "") -> None:
        super().__init__(f"{kind.value}: {message}")
        self.kind = kind
        self.retryable = kind in RETRYABLE


class TaskValidationError(Exception):
    """A task file is malformed or inconsistent with its oracle."""
