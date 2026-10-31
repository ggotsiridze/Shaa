from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scopeshift.loader import load_tasks
from scopeshift.schema import Task

TASK_DIR = ROOT / "tasks" / "development"


@pytest.fixture(scope="session")
def tasks() -> list[Task]:
    return load_tasks(TASK_DIR)


@pytest.fixture(scope="session")
def by_id(tasks) -> dict[str, Task]:
    return {t.task_id: t for t in tasks}


def raw_task(task_id: str) -> dict:
    return json.loads((TASK_DIR / f"{task_id}.json").read_text())


def answer_json(**overrides) -> str:
    base = {
        "exception_status": "ACTIVE",
        "transition_event_id": None,
        "governing_rule_id": "CHG-02",
        "primary_action": "DOCUMENT",
        "evidence_ids": ["E1", "E4", "E6"],
        "rationale": "x",
    }
    base.update(overrides)
    return json.dumps(base)


def reference_json(task: Task) -> str:
    r = task.hidden_reference
    return answer_json(
        exception_status=r.exception_status.value,
        transition_event_id=r.transition_event_id,
        governing_rule_id=r.governing_rule_id,
        primary_action=r.primary_action.value,
        evidence_ids=list(r.required_evidence_ids),
    )


# ---- synthetic maintenance task builder for oracle unit tests -----------------------

AP = "AUTHORITATIVE_POLICY_SOURCE"
AT = "AUTHORITATIVE_SECURITY_TELEMETRY"
INF = "INTERNAL_INFORMATIONAL_SOURCE"


def ev(eid, ts, kind=None, src="edr", cls=AT, details=None):
    e = {"event_id": eid, "timestamp": ts, "source": src, "source_class": cls, "event": f"event {eid}"}
    if details:
        e["details"] = details
    return e, ({"event_id": eid, "kind": kind} if kind else None)


def create(eid, ts, start, end, src="change_management", cls=AP, sinks=None):
    d = {"valid_from": start, "valid_until": end}
    if sinks:
        d["approved_sinks"] = sinks
    return ev(eid, ts, "AUTHORIZATION_CREATE", src, cls, d)


def extend(eid, ts, new_end, src="change_management", cls=AP):
    return ev(eid, ts, "AUTHORIZATION_EXTEND", src, cls, {"new_valid_until": new_end})


def revoke(eid, ts, src="change_management", cls=AP):
    return ev(eid, ts, "AUTHORIZATION_REVOKE", src, cls)


def activity(eid, ts):
    return ev(eid, ts, "SCOPE_ACTIVITY")


def transfer(eid, ts, dest, classification="PROTECTED", src="dlp", cls=AT):
    return ev(eid, ts, "DATA_TRANSFER", src, cls, {"destination_ip": dest, "data_classification": classification})


def build_task(events, *, reference=None) -> Task:
    """Build a valid Task around synthetic events, using SS-DEV-001A's policy/rule map.
    The reference is filled from the oracle unless given (tests compare to the oracle directly)."""
    from scopeshift.oracle import run_oracle

    base = raw_task("SS-DEV-001A")
    timeline = [e for e, _ in events]
    sems = [s for _, s in events if s]
    last = timeline[-1]
    base["model_visible"]["timeline"] = timeline
    base["model_visible"]["alert"]["timestamp"] = last["timestamp"]
    base["model_visible"]["alert"]["related_event_id"] = last["event_id"]
    spec = base["oracle_spec"]
    spec["event_semantics"] = sems
    spec["evaluated_event_id"] = last["event_id"]
    spec["authority"] = spec["authority"] + [
        {"capability": "ESTABLISH_STOP_CONDITION", "source": "dlp", "source_class": AT}]
    spec["rule_map"]["stop_condition"] = {"rule_id": "CHG-04", "action": "OPEN_INCIDENT"}
    spec["rule_map"]["no_exception"] = {"rule_id": "CHG-04", "action": "ESCALATE_TIER2"}
    base["hidden_reference"] = reference or {
        "exception_status": "ACTIVE", "transition_event_id": None, "governing_rule_id": "CHG-02",
        "primary_action": "DOCUMENT", "required_evidence_ids": [timeline[0]["event_id"]]}
    task = Task.model_validate(copy.deepcopy(base))
    if reference is None:
        res = run_oracle(task)
        base["hidden_reference"] = {
            "exception_status": res.exception_status.value, "transition_event_id": res.transition_event_id,
            "governing_rule_id": res.governing_rule_id, "primary_action": res.primary_action.value,
            "required_evidence_ids": res.required_evidence_ids[:4]}
        task = Task.model_validate(base)
    return task
