"""Load and structurally validate task files."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from scopeshift.errors import TaskValidationError
from scopeshift.schema import Task


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate JSON key {key!r}")
        out[key] = value
    return out


def load_task(path: Path) -> Task:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, ValueError) as exc:
        raise TaskValidationError(f"{path}: cannot read JSON: {exc}") from exc
    try:
        task = Task.model_validate(raw)
    except ValidationError as exc:
        raise TaskValidationError(f"{path}: schema error: {exc}") from exc
    if path.stem != task.task_id:
        raise TaskValidationError(f"{path}: file name does not match task_id {task.task_id}")
    return task


def load_tasks(directory: Path) -> list[Task]:
    if not directory.is_dir():
        raise TaskValidationError(f"task directory not found: {directory}")
    stray = sorted(p.name for p in directory.glob("*.json") if p.name != "pairs.json" and not p.name.startswith("SS-"))
    if stray:
        raise TaskValidationError(f"unexpected JSON files in {directory} (task files must be SS-*.json): {stray}")
    files = sorted(directory.glob("SS-*.json"))
    if not files:
        raise TaskValidationError(f"no task files in {directory}")
    tasks = [load_task(f) for f in files]
    ids = [t.task_id for t in tasks]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise TaskValidationError(f"duplicate task ids: {sorted(dupes)}")
    return tasks


def load_pairs(directory: Path) -> dict[str, dict]:
    path = directory / "pairs.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, ValueError) as exc:
        raise TaskValidationError(f"{path}: cannot read pairs manifest: {exc}") from exc
