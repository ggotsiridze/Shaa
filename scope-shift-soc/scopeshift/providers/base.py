"""Provider interface. A provider sees ONLY a fresh (system, user) prompt per call."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from scopeshift.prompt import Prompt


@dataclass(frozen=True)
class ProviderResponse:
    """A COMPLETED provider call. `text` is the model's raw output, exactly as returned.

    Usage fields are None when the provider did not report them (never guessed). `cost_usd`
    is None for real providers: no pricing is hard-coded, so cost is not computed."""

    text: str | None
    input_tokens: int | None = 0
    output_tokens: int | None = 0
    total_tokens: int | None = None
    cost_usd: float | None = 0.0
    response_id: str | None = None
    returned_model: str | None = None
    status: str | None = None
    raw_meta: dict = field(default_factory=dict)


class Provider(ABC):
    name: str = "base"
    paid: bool = False  # True -> CLI shows the plan and requires confirmation before calling

    def __init__(self, model: str, settings: dict | None = None) -> None:
        self.model = model
        self.settings = dict(settings or {})

    @abstractmethod
    def complete(self, prompt: Prompt) -> ProviderResponse:
        """Return a completed response, or raise errors.InfrastructureError.

        Must be stateless across calls: no conversation history, no cached answers."""
