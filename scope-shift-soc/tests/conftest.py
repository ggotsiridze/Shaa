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


# ---- synthetic task builders for oracle unit tests ------------------------------------
# Source classes are descriptive only; authority comes from oracle_spec.authority.

CR = "CHANGE_RECORD"
TR = "TICKET_RECORD"
ER = "ENGAGEMENT_RECORD"
EP = "ENDPOINT_TELEMETRY"
NET = "NETWORK_TELEMETRY"
DLP = "DLP_TELEMETRY"
CHAT = "CHAT_MESSAGE"
SYS = "SYSTEM_EVENT"
HOST, ACCT = "app-db-02.corp.example", "svc-patch"


def ev(eid, ts, kind=None, src="edr", cls=EP, details=None):
    e = {"event_id": eid, "timestamp": ts, "source": src, "source_class": cls, "event": f"event {eid}"}
    if details:
        e["details"] = details
    return e, ({"event_id": eid, "kind": kind} if kind else None)


# maintenance helpers
def create(eid, ts, start, end, src="change_management", cls=CR, host=HOST, account=ACCT):
    return ev(eid, ts, "AUTHORIZATION_CREATE", src, cls,
              {"valid_from": start, "valid_until": end, "host": host, "account": account})


def extend(eid, ts, new_end, src="change_management", cls=CR):
    return ev(eid, ts, "AUTHORIZATION_EXTEND", src, cls, {"new_valid_until": new_end})


def revoke(eid, ts, src="change_management", cls=CR):
    return ev(eid, ts, "AUTHORIZATION_REVOKE", src, cls)


def activity(eid, ts, host=HOST, account=ACCT):
    return ev(eid, ts, "ACTIVITY", details={"host": host, "account": account})


def marker(eid, ts):
    return ev(eid, ts, "TIME_MARKER", "system_clock", SYS, {"marker_time": ts})


# pentest helpers
PT_METHODS = "password_spraying,psexec_lateral_movement,lsass_credential_dumping,exfiltration_simulation"
INGRESS, SINK, SUFFIX = "198.51.100.23", "203.0.113.50", ".stg.corp.example"


def roe(eid, ts, start="2026-10-30T09:00:00Z", end="2026-10-30T17:00:00Z", methods=PT_METHODS,
        lateral="psexec_lateral_movement", sinks=SINK, src="engagement_register", cls=ER):
    return ev(eid, ts, "AUTHORIZATION_CREATE", src, cls, {
        "valid_from": start, "valid_until": end, "approved_ingress_source": INGRESS,
        "approved_target_suffix": SUFFIX, "approved_methods": methods, "lateral_methods": lateral,
        "approved_sinks": sinks})


def pt_ingress(eid, ts, src_ip=INGRESS, dst_host="stg-dc-01" + SUFFIX, technique="password_spraying"):
    return ev(eid, ts, "ACTIVITY", "netflow", NET, {"src_ip": src_ip, "dst_host": dst_host, "technique": technique})


def pt_lateral(eid, ts, src_host="stg-web-01" + SUFFIX, dst_host="stg-app-02" + SUFFIX, technique="psexec_lateral_movement"):
    return ev(eid, ts, "ACTIVITY", "edr", EP, {"src_host": src_host, "dst_host": dst_host, "technique": technique})


def pt_on_host(eid, ts, host="stg-app-02" + SUFFIX, technique="lsass_credential_dumping"):
    return ev(eid, ts, "ACTIVITY", "edr", EP, {"host": host, "technique": technique})


def transfer(eid, ts, dest, classification="PROTECTED", src="dlp", cls=DLP, src_host="stg-app-02" + SUFFIX):
    return ev(eid, ts, "DATA_TRANSFER", src, cls,
              {"src_host": src_host, "destination_ip": dest, "data_classification": classification})


def _finish(base: dict, events, reference) -> Task:
    from scopeshift.oracle import run_oracle

    timeline = [e for e, _ in events]
    last = timeline[-1]
    base["model_visible"]["timeline"] = timeline
    base["model_visible"]["alert"]["timestamp"] = last["timestamp"]
    base["model_visible"]["alert"]["related_event_id"] = last["event_id"]
    spec = base["oracle_spec"]
    spec["event_semantics"] = [s for _, s in events if s]
    spec["evaluated_event_id"] = last["event_id"]
    base["hidden_reference"] = reference or {
        "exception_status": "ACTIVE", "transition_event_id": None, "governing_rule_id": spec["rule_map"]["covered"]["rule_id"],
        "primary_action": spec["rule_map"]["covered"]["action"], "required_evidence_ids": [timeline[0]["event_id"]]}
    task = Task.model_validate(copy.deepcopy(base))
    if reference is None:
        res = run_oracle(task)
        base["hidden_reference"] = {
            "exception_status": res.exception_status.value, "transition_event_id": res.transition_event_id,
            "governing_rule_id": res.governing_rule_id, "primary_action": res.primary_action.value,
            "required_evidence_ids": res.required_evidence_ids[:4]}
        task = Task.model_validate(base)
    return task


def build_task(events, *, reference=None) -> Task:
    """Maintenance-model task around synthetic events, using SS-DEV-001A's policy/rule map.
    The reference is filled from the oracle unless given (tests compare to the oracle directly)."""
    base = raw_task("SS-DEV-001A")
    base["oracle_spec"]["rule_map"]["no_exception"] = {"rule_id": "CHG-04", "action": "ESCALATE_TIER2"}
    return _finish(base, events, reference)


def build_pt_task(events, *, reference=None) -> Task:
    """Pentest-model task around synthetic events, using SS-DEV-002A's policy/rule map."""
    base = raw_task("SS-DEV-002A")
    base["oracle_spec"]["rule_map"]["no_exception"] = {"rule_id": "PT-05", "action": "ESCALATE_TIER2"}
    return _finish(base, events, reference)
