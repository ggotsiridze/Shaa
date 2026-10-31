"""Provider interface. A provider sees ONLY a fresh (system, user) prompt per call."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from scopeshift.prompt import Prompt


@dataclass(frozen=True)
class ProviderResponse:
    text: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    raw_meta: dict = field(default_factory=dict)


class Provider(ABC):
    name: str = "base"

    def __init__(self, model: str, settings: dict | None = None) -> None:
        self.model = model
        self.settings = dict(settings or {})

    @abstractmethod
    def complete(self, prompt: Prompt) -> ProviderResponse:
        """Return a completed response, or raise errors.InfrastructureError.

        Must be stateless across calls: no conversation history, no cached answers."""
