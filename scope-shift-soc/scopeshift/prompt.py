"""Build the model-under-test prompt. Only `model_visible` is ever serialized."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from scopeshift.schema import Task

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
# v1: the coached pilot prompt (frozen, archived so pilot runs stay reproducible).
# v2: coaching removed; only the task, the answer contract and field definitions remain.
PROMPT_VERSIONS = {
    "v1": _PROMPTS_DIR / "archive" / "model_under_test_v1.md",
    "v2": _PROMPTS_DIR / "model_under_test_v2.md",
}
DEFAULT_PROMPT_VERSION = "v2"
PROMPT_PATH = PROMPT_VERSIONS[DEFAULT_PROMPT_VERSION]
SYSTEM_MARK = "<!-- SYSTEM -->"
USER_MARK = "<!-- USER -->"
PLACEHOLDER = "{{INCIDENT_JSON}}"


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str


def load_template(path: Path = PROMPT_PATH) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    if text.count(SYSTEM_MARK) != 1 or text.count(USER_MARK) != 1:
        raise ValueError(f"{path}: expected exactly one SYSTEM and one USER marker")
    system = text.split(SYSTEM_MARK, 1)[1].split(USER_MARK, 1)[0].strip()
    user = text.split(USER_MARK, 1)[1].strip()
    if user.count(PLACEHOLDER) != 1:
        raise ValueError(f"{path}: user template must contain {PLACEHOLDER} exactly once")
    return system, user


def render_incident(task: Task) -> str:
    visible = task.model_visible.model_dump(mode="json")
    return json.dumps(visible, indent=2, ensure_ascii=False)


def build_prompt(task: Task, path: Path = PROMPT_PATH) -> Prompt:
    system, user_template = load_template(path)
    return Prompt(system=system, user=user_template.replace(PLACEHOLDER, render_incident(task)))
