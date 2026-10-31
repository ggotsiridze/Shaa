"""Prompt/grader alignment check shared by scripts/validate_alignment.py and the tests."""

from __future__ import annotations

from pathlib import Path

from scopeshift.grader import PROMPT_REQUIREMENTS, WEIGHTS
from scopeshift.schema import ModelAnswer

# grader component -> answer field it reads
COMPONENT_FIELDS = {
    "exception_status": "exception_status",
    "transition_event_id": "transition_event_id",
    "governing_rule_id": "governing_rule_id",
    "primary_action": "primary_action",
    "evidence_coverage": "evidence_ids",
}


def check_alignment(root: Path, prompt_text: str | None = None) -> tuple[list[tuple[str, str, int]], list[str]]:
    from scopeshift.prompt import PROMPT_PATH

    prompt_path = PROMPT_PATH
    text = prompt_text if prompt_text is not None else prompt_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    doc = (root / "TASK_GRADER_ALIGNMENT.md").read_text(encoding="utf-8")
    rows: list[tuple[str, str, int]] = []
    problems: list[str] = []

    for comp in WEIGHTS:
        if comp not in PROMPT_REQUIREMENTS:
            problems.append(f"grader component {comp!r} has no declared prompt requirement")
        field = COMPONENT_FIELDS.get(comp)
        if field is None or field not in ModelAnswer.model_fields:
            problems.append(f"grader component {comp!r} reads no answer-schema field")
    for field in ModelAnswer.model_fields:
        if f'"{field}"' not in text:
            problems.append(f"answer-schema field {field!r} is never requested in the prompt")
    for req, phrases in PROMPT_REQUIREMENTS.items():
        if f"`{req}`" not in doc:
            problems.append(f"requirement {req!r} not documented in TASK_GRADER_ALIGNMENT.md")
        for phrase in phrases:
            hit = next((i for i, ln in enumerate(lines, 1) if phrase in ln), None)
            if hit is None:
                problems.append(f"requirement {req!r}: prompt does not contain {phrase!r}")
            else:
                rows.append((req, phrase, hit))
    return rows, problems
