import copy
import json

import pytest
from conftest import TASK_DIR, raw_task
from pydantic import ValidationError

from scopeshift.errors import TaskValidationError
from scopeshift.loader import load_task, load_tasks
from scopeshift.schema import Task


def test_valid_task(tasks):
    assert len(tasks) == 4
    assert {t.task_id for t in tasks} == {"SS-DEV-001A", "SS-DEV-001B", "SS-DEV-002A", "SS-DEV-002B"}
    assert all(t.internal_metadata.synthetic and t.internal_metadata.human_review_status == "PENDING" for t in tasks)


def test_invalid_task():
    bad = raw_task("SS-DEV-001A")
    del bad["model_visible"]["timeline"]
    with pytest.raises(ValueError):
        Task.model_validate(bad)


def test_duplicate_task_ids(tmp_path):
    for name in ("SS-DEV-001A", "SS-DEV-001B"):
        (tmp_path / f"{name}.json").write_text((TASK_DIR / "SS-DEV-001A.json").read_text())
    with pytest.raises(TaskValidationError, match="does not match task_id"):
        load_tasks(tmp_path)


def test_duplicate_task_ids_rejected_by_harness(tasks):
    from scopeshift.harness import run_eval
    from scopeshift.providers.mock import MockProvider

    with pytest.raises(ValueError, match="duplicate task ids"):
        run_eval([tasks[0], tasks[0]], MockProvider.from_tasks("mock-perfect", tasks), trials=1)


def test_duplicate_event_ids():
    bad = raw_task("SS-DEV-001A")
    bad["model_visible"]["timeline"][1]["event_id"] = "E1"
    with pytest.raises(ValueError, match="duplicate event ids"):
        Task.model_validate(bad)


def test_unknown_source_class():
    bad = raw_task("SS-DEV-001A")
    bad["model_visible"]["timeline"][3]["source_class"] = "VERY_TRUSTWORTHY_SOURCE"
    with pytest.raises(ValueError):
        Task.model_validate(bad)


def test_invalid_action_enum():
    bad = raw_task("SS-DEV-001A")
    bad["hidden_reference"]["primary_action"] = "PANIC"
    with pytest.raises(ValueError):
        Task.model_validate(bad)


def test_naive_timestamp_rejected():
    bad = raw_task("SS-DEV-001A")
    bad["model_visible"]["timeline"][0]["timestamp"] = "2026-10-30T03:00:00"
    with pytest.raises(ValueError):
        Task.model_validate(bad)


def test_unknown_task_field_rejected():
    bad = raw_task("SS-DEV-001A")
    bad["notes"] = "x"
    with pytest.raises(ValueError):
        Task.model_validate(bad)


def test_non_chronological_timeline_rejected():
    bad = raw_task("SS-DEV-001A")
    tl = bad["model_visible"]["timeline"]
    tl[1]["timestamp"], tl[2]["timestamp"] = tl[2]["timestamp"], tl[1]["timestamp"]
    with pytest.raises(ValueError, match="chronological"):
        Task.model_validate(bad)


def test_reference_inconsistent_transition_rejected():
    bad = raw_task("SS-DEV-001A")
    bad["hidden_reference"]["transition_event_id"] = "E6"  # ACTIVE with a transition
    with pytest.raises(ValueError):
        Task.model_validate(bad)


def test_reference_unknown_rule_rejected():
    bad = raw_task("SS-DEV-001A")
    bad["hidden_reference"]["governing_rule_id"] = "CHG-99"
    with pytest.raises(ValueError):
        Task.model_validate(bad)


def test_corrupt_json_file(tmp_path):
    p = tmp_path / "SS-DEV-001A.json"
    p.write_text('{"task_id": "SS-DEV-001A",')
    with pytest.raises(TaskValidationError, match="cannot read JSON"):
        load_task(p)


def test_duplicate_json_keys_in_task_file(tmp_path):
    text = (TASK_DIR / "SS-DEV-001A.json").read_text()
    p = tmp_path / "SS-DEV-001A.json"
    p.write_text(text.replace('"task_id": "SS-DEV-001A",', '"task_id": "SS-DEV-001A", "task_id": "SS-DEV-001A",', 1))
    with pytest.raises(TaskValidationError, match="duplicate JSON key"):
        load_task(p)


def test_missing_task_dir(tmp_path):
    with pytest.raises(TaskValidationError, match="not found"):
        load_tasks(tmp_path / "nope")


def test_empty_task_dir(tmp_path):
    with pytest.raises(TaskValidationError, match="no task files"):
        load_tasks(tmp_path)


def test_task_files_are_valid_json_with_stable_ids():
    for p in TASK_DIR.glob("SS-*.json"):
        assert json.loads(p.read_text())["task_id"] == p.stem


def test_tasks_frozen(tasks):
    with pytest.raises(ValidationError):
        tasks[0].task_id = "X"  # type: ignore[misc]
    t = copy.deepcopy(tasks[0])
    assert t == tasks[0]
