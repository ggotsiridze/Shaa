"""Deterministic mock providers. No API key, no network.

A mock only receives the prompt, like a real provider. It recognizes which task it is looking
at by hashing the prompt against an answer key built from the task files. This is test
scaffolding: the answer key never reaches the model prompt.
"""

from __future__ import annotations

import hashlib
import json
import threading

from scopeshift.errors import InfraKind, InfrastructureError
from scopeshift.oracle import run_oracle
from scopeshift.prompt import Prompt, build_prompt
from scopeshift.providers.base import Provider, ProviderResponse
from scopeshift.schema import Action, Task

MOCK_MODELS = (
    "mock-perfect",
    "mock-wrong-action",
    "mock-missed-transition",
    "mock-premature-transition",
    "mock-source-authority-confusion",
    "mock-malformed",
    "mock-empty",
    "mock-timeout",
    "mock-flaky",  # one 429 per prompt, then a perfect answer: exercises bounded retry
)


def prompt_key(prompt: Prompt) -> str:
    return hashlib.sha256(f"{prompt.system}\x00{prompt.user}".encode()).hexdigest()


def _answer(status: str, transition: str | None, rule: str, action: str, evidence: list[str]) -> str:
    return json.dumps({
        "exception_status": status,
        "transition_event_id": transition,
        "governing_rule_id": rule,
        "primary_action": action,
        "evidence_ids": evidence,
        "rationale": "Mock answer.",
    })


def _perfect(task: Task) -> str:
    r = task.hidden_reference
    return _answer(r.exception_status.value, r.transition_event_id, r.governing_rule_id,
                   r.primary_action.value, list(r.required_evidence_ids))


def _wrong_action(task: Task) -> str:
    r = task.hidden_reference
    wrong = Action.ESCALATE_TIER2 if r.primary_action != Action.ESCALATE_TIER2 else Action.DOCUMENT
    return _answer(r.exception_status.value, r.transition_event_id, r.governing_rule_id,
                   wrong.value, list(r.required_evidence_ids))


def _always_active(task: Task) -> str:
    """Tracks authorization correctly but never notices that it stopped governing."""
    covered = task.oracle_spec.rule_map.covered
    cited = [*run_oracle(task).authorizing_event_ids, task.oracle_spec.evaluated_event_id]
    return _answer("ACTIVE", None, covered.rule_id, covered.action.value, cited[:4])


def _terminate_at_alert(task: Task) -> str:
    rm = task.oracle_spec.rule_map
    outcome = rm.stop_condition or rm.expired or rm.revoked
    assert outcome is not None
    first, alerted = task.model_visible.timeline[0].event_id, task.oracle_spec.evaluated_event_id
    return _answer("TERMINATED", alerted, outcome.rule_id, outcome.action.value, [first, alerted])


def _authority_blind(task: Task) -> str:
    res = run_oracle(task, enforce_source_authority=False)
    return _answer(res.exception_status.value, res.transition_event_id, res.governing_rule_id,
                   res.primary_action.value, res.required_evidence_ids)


BEHAVIOURS = {
    "mock-perfect": _perfect,
    "mock-flaky": _perfect,
    "mock-wrong-action": _wrong_action,
    "mock-missed-transition": _always_active,
    "mock-premature-transition": _terminate_at_alert,
    "mock-source-authority-confusion": _authority_blind,
    "mock-malformed": lambda task: '{"exception_status": "TERMINATED", "transition_event_id": "E',
    "mock-empty": lambda task: "",
}


class MockProvider(Provider):
    name = "mock"

    def __init__(self, model: str, answer_key: dict[str, Task], settings: dict | None = None) -> None:
        if model not in MOCK_MODELS:
            raise ValueError(f"unknown mock model {model!r}; choose from {MOCK_MODELS}")
        super().__init__(model, settings)
        self._key = dict(answer_key)
        self._flaky_seen: set[str] = set()
        self._lock = threading.Lock()
        self.calls = 0

    @classmethod
    def from_tasks(cls, model: str, tasks: list[Task], settings: dict | None = None) -> MockProvider:
        key: dict[str, Task] = {}
        for t in tasks:
            k = prompt_key(build_prompt(t))
            if k in key:
                raise ValueError(f"{t.task_id} and {key[k].task_id} render identical prompts")
            key[k] = t
        return cls(model, key, settings)

    def complete(self, prompt: Prompt) -> ProviderResponse:
        with self._lock:
            self.calls += 1
        if self.model == "mock-timeout":
            raise InfrastructureError(InfraKind.TIMEOUT, "mock request timed out")
        key = prompt_key(prompt)
        task = self._key.get(key)
        if task is None:
            raise InfrastructureError(InfraKind.TRANSPORT, "mock has no answer for this prompt")
        if self.model == "mock-flaky":
            with self._lock:
                first = key not in self._flaky_seen
                self._flaky_seen.add(key)
            if first:
                raise InfrastructureError(InfraKind.RATE_LIMIT, "429 mock rate limit")
        return ProviderResponse(text=BEHAVIOURS[self.model](task))
