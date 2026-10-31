"""Provider factory. Adding a provider = one module implementing `Provider` + one entry here;
tasks, prompt, grader and metrics stay untouched."""

from __future__ import annotations

from scopeshift.providers.base import Provider
from scopeshift.schema import Task

PROVIDER_NAMES = ("mock", "openai")


def build_provider(name: str, model: str, tasks: list[Task], settings: dict | None = None) -> Provider:
    if name == "mock":
        from scopeshift.providers.mock import MockProvider

        return MockProvider.from_tasks(model, tasks, settings)
    if name == "openai":
        from scopeshift.providers.openai_provider import OpenAIProvider

        return OpenAIProvider(model, settings)
    raise ValueError(f"unknown provider {name!r}; choose from {PROVIDER_NAMES}")
