"""Deterministic parser and grader. No embeddings, no LLM judge."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from scopeshift.schema import ModelAnswer, Task

WEIGHTS = {
    "exception_status": 0.25,
    "transition_event_id": 0.25,
    "governing_rule_id": 0.20,
    "primary_action": 0.20,
    "evidence_coverage": 0.10,
}

# Every scored/enforced requirement and the phrases the prompt must contain to request it.
# Checked by scripts/validate_alignment.py and tests/test_alignment.py.
PROMPT_REQUIREMENTS = {
    "exception_status": ['"exception_status"', '"ACTIVE"', '"TERMINATED"', '"NOT_APPLICABLE"'],
    "transition_event_id": ['"transition_event_id"', "first event", "null if exception_status is not TERMINATED"],
    "governing_rule_id": ['"governing_rule_id"', "single policy rule that prescribes the required action"],
    "primary_action": [
        '"primary_action"', '"DOCUMENT"', '"MONITOR"', '"ESCALATE_TIER2"',
        '"OPEN_INCIDENT"', '"ISOLATE_HOST"', '"REVOKE_SESSION"', "exactly one of",
    ],
    "evidence_coverage": [
        '"evidence_ids"', "1 to 4 distinct event_id", "Cite every event necessary",
        "created the authorization and any permitted change to it",
        "claimed to create, extend, modify or revoke the authorization but did not take effect",
        "if exception_status is TERMINATED, the transition event",
        "the most recent event that was evaluated against that stop condition",
    ],
    "rationale_limit": ['"rationale"', "at most 600 characters"],
    "single_json_object": ["Return only the required JSON object", "exactly these six fields and no others"],
    "single_value": ["single value", "lists of alternatives are not accepted"],
}

# A whole-response ```json fence is tolerated; nothing may precede or follow it.
_FENCE = re.compile(r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*)\r?\n```\Z", re.DOTALL)


class ParseError(Exception):
    pass


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for k, v in pairs:
        if k in out:
            raise ParseError(f"duplicate key {k!r} (contradictory answers)")
        out[k] = v
    return out


def parse_response(raw: object) -> ModelAnswer:
    """Parse a raw model response into a ModelAnswer, or raise ParseError. Fails closed."""
    if not isinstance(raw, str):
        raise ParseError(f"response is {type(raw).__name__}, not str")
    text = raw.strip()
    if not text:
        raise ParseError("empty response")
    m = _FENCE.match(text)
    if m:
        text = m.group("body").strip()
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except ParseError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ParseError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ParseError(f"top-level JSON is {type(data).__name__}, not object")
    try:
        return ModelAnswer.model_validate(data)
    except ValidationError as exc:
        raise ParseError(f"schema violation: {exc.errors(include_url=False)}") from exc


@dataclass
class Grade:
    score: float
    strict_pass: bool
    components: dict[str, float]
    unknown_event_ids: list[str] = field(default_factory=list)
    unknown_rule_id: bool = False


def grade(task: Task, answer: ModelAnswer) -> Grade:
    ref = task.hidden_reference
    event_ids = {e.event_id for e in task.model_visible.timeline}
    rule_ids = {r.id for r in task.model_visible.organization_policy.rules}

    cited = list(answer.evidence_ids)
    unknown = [e for e in cited if e not in event_ids]
    if answer.transition_event_id is not None and answer.transition_event_id not in event_ids:
        unknown.append(answer.transition_event_id)
    required = set(ref.required_evidence_ids)
    coverage = len(required & set(cited)) / len(required)

    components = {
        "exception_status": float(answer.exception_status == ref.exception_status.value),
        "transition_event_id": float(answer.transition_event_id == ref.transition_event_id),
        "governing_rule_id": float(answer.governing_rule_id == ref.governing_rule_id),
        "primary_action": float(answer.primary_action == ref.primary_action.value),
        "evidence_coverage": 0.0 if unknown else coverage,
    }
    score = round(sum(WEIGHTS[k] * v for k, v in components.items()), 6)
    strict = (
        components["exception_status"] == 1.0
        and components["transition_event_id"] == 1.0
        and components["governing_rule_id"] == 1.0
        and components["primary_action"] == 1.0
        and components["evidence_coverage"] == 1.0
        and not unknown
    )
    return Grade(score, strict, components, unknown, answer.governing_rule_id not in rule_ids)
