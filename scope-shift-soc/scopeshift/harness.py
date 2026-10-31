"""Trial runner. Each trial is independent: fresh prompt, no history, unique trial_id."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from scopeshift.errors import InfrastructureError
from scopeshift.grader import ParseError, grade, parse_response
from scopeshift.prompt import build_prompt
from scopeshift.providers.base import Provider, ProviderResponse
from scopeshift.schema import Task
from scopeshift.taxonomy import Failure, classify

DEFAULT_MAX_ATTEMPTS = 3


@dataclass
class TrialRecord:
    run_id: str
    trial_id: str
    trial_index: int
    task_id: str
    pair_id: str
    model_id: str
    provider: str
    settings: dict
    timestamp: str
    raw_response: str | None = None
    parsed_response: dict | None = None
    parse_error: str | None = None
    score: float | None = None
    components: dict | None = None
    strict_pass: bool = False
    failure_types: list[str] = field(default_factory=list)
    latency_s: float = 0.0
    attempt_count: int = 0
    infrastructure_failure: bool = False
    infrastructure_error: str | None = None
    model_failure: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def call_with_retries(
    provider: Provider,
    prompt,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_s: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[ProviderResponse | None, int, InfrastructureError | None]:
    """Retry ONLY retryable infrastructure errors, at most max_attempts calls total.

    A completed response is returned as-is, even if it is wrong or unparseable."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    last_err: InfrastructureError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return provider.complete(prompt), attempt, None
        except InfrastructureError as exc:
            last_err = exc
            if not exc.retryable or attempt == max_attempts:
                return None, attempt, exc
            sleep(backoff_s * (2 ** (attempt - 1)))
    return None, max_attempts, last_err  # pragma: no cover


def run_trial(task: Task, provider: Provider, run_id: str, trial_index: int,
              max_attempts: int = DEFAULT_MAX_ATTEMPTS, backoff_s: float = 0.0) -> TrialRecord:
    prompt = build_prompt(task)  # rebuilt per trial from immutable task data
    rec = TrialRecord(
        run_id=run_id,
        trial_id=str(uuid.uuid4()),
        trial_index=trial_index,
        task_id=task.task_id,
        pair_id=task.internal_metadata.pair_id,
        model_id=provider.model,
        provider=provider.name,
        settings=dict(provider.settings),
        timestamp=datetime.now(UTC).isoformat(),
    )
    start = time.perf_counter()
    response, attempts, err = call_with_retries(provider, prompt, max_attempts, backoff_s)
    rec.latency_s = round(time.perf_counter() - start, 6)
    rec.attempt_count = attempts

    if err is not None:
        rec.infrastructure_failure = True
        rec.infrastructure_error = str(err)
        rec.failure_types = [Failure.INFRASTRUCTURE_FAILURE.value]
        return rec

    assert response is not None
    rec.raw_response = response.text
    rec.input_tokens, rec.output_tokens, rec.cost_usd = response.input_tokens, response.output_tokens, response.cost_usd
    try:
        answer = parse_response(response.text)
    except ParseError as exc:
        rec.parse_error = str(exc)
        rec.score = 0.0
        rec.model_failure = True
        rec.failure_types = [Failure.PARSE_FAILURE.value]
        return rec

    g = grade(task, answer)
    rec.parsed_response = answer.model_dump()
    rec.score, rec.components, rec.strict_pass = g.score, g.components, g.strict_pass
    rec.model_failure = not g.strict_pass
    rec.failure_types = [f.value for f in classify(task, answer, g.strict_pass)]
    return rec


def run_eval(tasks: list[Task], provider: Provider, trials: int, run_id: str | None = None,
             max_attempts: int = DEFAULT_MAX_ATTEMPTS, backoff_s: float = 0.0) -> tuple[str, list[TrialRecord]]:
    if trials < 1:
        raise ValueError("trials must be >= 1")
    ids = [t.task_id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate task ids in run: {ids}")
    run_id = run_id or f"run-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    records = [run_trial(t, provider, run_id, i, max_attempts, backoff_s)
               for i in range(trials) for t in tasks]
    return run_id, records
