"""Provider factory. Adding a provider = one module implementing `Provider` + one entry here;
tasks, prompt, grader and metrics stay untouched."""

from __future__ import annotations

from scopeshift.providers.base import Provider
from scopeshift.schema import Task

PROVIDER_NAMES = ("mock", "openai", "anthropic")


def build_provider(name: str, model: str, tasks: list[Task], settings: dict | None = None,
                   prompt_path=None) -> Provider:
    if name == "mock":
        from scopeshift.providers.mock import MockProvider

        return MockProvider.from_tasks(model, tasks, settings, prompt_path)
    if name == "openai":
        from scopeshift.providers.openai_provider import OpenAIProvider

        return OpenAIProvider(model, settings)
    if name == "anthropic":
        from scopeshift.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(model, settings)
    raise ValueError(f"unknown provider {name!r}; choose from {PROVIDER_NAMES}")


def provider_module(name: str):
    """The real-provider module (key env var, key status, settings validation), or None for mock."""
    if name == "openai":
        from scopeshift.providers import openai_provider as mod
    elif name == "anthropic":
        from scopeshift.providers import anthropic_provider as mod
    else:
        return None
    return mod
