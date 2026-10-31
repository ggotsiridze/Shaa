import copy

from conftest import TASK_DIR, raw_task

from scopeshift.loader import load_pairs
from scopeshift.pair_validation import diff_visible, validate_all_pairs, validate_pair
from scopeshift.schema import Task


def test_pair_diff_matches_expected(tasks):
    reports = validate_all_pairs(load_pairs(TASK_DIR), tasks)
    assert [r.pair_id for r in reports] == ["DEV-PAIR-001", "DEV-PAIR-002"]
    for r in reports:
        assert r.ok, r.problems
    r1 = reports[0]
    assert set(r1.visible_diff) == {"timeline[E4].source", "timeline[E4].source_class"}
    assert r1.visible_diff["timeline[E4].source_class"] == ("CHANGE_RECORD", "TICKET_RECORD")


def test_pair1_extension_text_identical(by_id):
    a = by_id["SS-DEV-001A"].model_visible.timeline[3]
    b = by_id["SS-DEV-001B"].model_visible.timeline[3]
    assert a.event == b.event and a.details == b.details and a.timestamp == b.timestamp


def _mutated(task_id, fn):
    raw = raw_task(task_id)
    fn(raw)
    return Task.model_validate(raw)


def _check(by_id, b_task, pair="DEV-PAIR-001"):
    spec = load_pairs(TASK_DIR)[pair]
    tasks = dict(by_id)
    tasks[b_task.task_id] = b_task
    return validate_pair(pair, spec, tasks)


def test_unexpected_timestamp_change_fails(by_id):
    def f(r):
        r["model_visible"]["timeline"][4]["timestamp"] = "2026-10-30T03:52:31Z"
    rep = _check(by_id, _mutated("SS-DEV-001B", f))
    assert not rep.ok and any("timeline[E5].timestamp" in p for p in rep.problems)


def test_unexpected_policy_wording_change_fails(by_id):
    def f(r):
        r["model_visible"]["organization_policy"]["rules"][0]["text"] += " "
    assert not _check(by_id, _mutated("SS-DEV-001B", f)).ok


def test_unexpected_asset_or_name_change_fails(by_id):
    def f(r):
        r["model_visible"]["asset_context"]["criticality"] = "low"
    assert not _check(by_id, _mutated("SS-DEV-001B", f)).ok

    def g(r):
        r["model_visible"]["timeline"][1]["details"]["account"] = "svc-patch2"
    assert not _check(by_id, _mutated("SS-DEV-001B", g)).ok


def test_missing_declared_difference_fails(by_id):
    def f(r):
        e4 = r["model_visible"]["timeline"][3]
        e4["source"], e4["source_class"] = "change_management", "CHANGE_RECORD"
        r["hidden_reference"] = raw_task("SS-DEV-001A")["hidden_reference"]
    rep = _check(by_id, _mutated("SS-DEV-001B", f))
    assert not rep.ok and any("not present" in p for p in rep.problems)


def test_event_order_change_detected(by_id):
    a = by_id["SS-DEV-002A"]
    raw = copy.deepcopy(a.model_dump(mode="json"))
    raw["model_visible"]["timeline"].insert(5, {"event_id": "E8", "timestamp": "2026-10-30T11:20:00Z", "source": "edr",
                                                "source_class": "ENDPOINT_TELEMETRY", "event": "x"})
    diff = diff_visible(a, Task.model_validate(raw))
    assert "timeline.__order__" in diff and "timeline[E8].event" in diff


def test_metadata_mismatch_fails(by_id):
    def f(r):
        r["internal_metadata"]["pair_id"] = "DEV-PAIR-002"
    assert not _check(by_id, _mutated("SS-DEV-001B", f)).ok


def test_orphan_task_fails(tasks):
    pairs = load_pairs(TASK_DIR)
    del pairs["DEV-PAIR-002"]
    reports = validate_all_pairs(pairs, tasks)
    assert any(r.pair_id == "<orphans>" and not r.ok for r in reports)
