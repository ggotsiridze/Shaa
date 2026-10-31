"""Regression tests for the external-review repairs (REVIEW-011 .. REVIEW-021)."""

import json
import re

import pytest
from conftest import (
    activity,
    build_task,
    create,
    raw_task,
    reference_json,
)

from scopeshift.grader import grade, parse_response
from scopeshift.harness import TrialRecord
from scopeshift.metrics import compute_metrics
from scopeshift.oracle import State, check_against_reference, run_oracle
from scopeshift.schema import SourceClass, Task

VERDICT_WORDS = ("AUTHORIT", "TRUST", "VALID", "INFORMATIONAL", "OFFICIAL", "VERIFIED", "SIGNED", "LEGIT")


def _answer(task, **over):
    base = json.loads(reference_json(task))
    base.update(over)
    return grade(task, parse_response(json.dumps(base)))


# ---- Pair 1 ------------------------------------------------------------------------------

def test_change_record_no_signature_requirement(tasks):
    for t in tasks:
        texts = [r.text for r in t.model_visible.organization_policy.rules]
        texts += [e.event for e in t.model_visible.timeline]
        for text in texts:
            assert not re.search(r"\bsign(ed|ature)", text, re.IGNORECASE), (t.task_id, text)


def test_authoritative_extension_keeps_active(by_id):
    r = run_oracle(by_id["SS-DEV-001A"])
    assert r.exception_status.value == "ACTIVE" and r.transition_event_id is None
    assert r.authorizing_event_ids == ["E1", "E4"]
    assert r.event_states["E6"] == State.ACTIVE  # boundary marker passes without effect
    assert r.event_coverage["E7"] is True


def test_ticket_comment_extension_rejected(by_id):
    r = run_oracle(by_id["SS-DEV-001B"])
    assert r.rejected_claims == ["E4:AUTHORIZATION_EXTEND:unauthorized_source"]
    assert r.authorizing_event_ids == ["E1"]


def test_expiry_occurs_at_clock_boundary_event(by_id):
    t = by_id["SS-DEV-001B"]
    r = run_oracle(t)
    e6 = t.model_visible.timeline[5]
    assert (e6.event_id, e6.source, e6.source_class.value) == ("E6", "system_clock", "SYSTEM_EVENT")
    assert r.transition_event_id == "E6" and e6.timestamp == r.effective_window_end
    assert r.event_states["E5"] == State.ACTIVE and r.event_states["E6"] == State.TERMINATED


def test_post_expiry_activity_not_transition_event(by_id):
    t = by_id["SS-DEV-001B"]
    assert run_oracle(t).transition_event_id != "E7"
    res = _answer(t, transition_event_id="E7")
    assert not res.strict_pass and res.components["transition_event_id"] == 0.0


def test_task_validation_requires_boundary_marker_for_expiry():
    # Expiry with no event exactly at the window end -> the design rule flags the task.
    t = build_task([create("E1", "2026-10-30T03:00:00Z", "2026-10-30T03:00:00Z", "2026-10-30T04:00:00Z"),
                    activity("E2", "2026-10-30T04:15:00Z")])
    assert any("add a boundary TIME_MARKER" in p for p in check_against_reference(t))


def test_boundary_marker_identical_in_both_variants(by_id):
    a, b = by_id["SS-DEV-001A"].model_visible.timeline[5], by_id["SS-DEV-001B"].model_visible.timeline[5]
    assert a == b


def test_source_class_name_does_not_encode_authority_semantics(tasks):
    for sc in SourceClass:
        assert not any(w in sc.value for w in VERDICT_WORDS), sc.value
    for t in tasks:
        for e in t.model_visible.timeline:
            assert not any(w in e.source_class.value for w in VERDICT_WORDS)
            assert not any(w.lower() in e.source.lower() for w in VERDICT_WORDS)


def test_system_event_cannot_hold_authority():
    raw = raw_task("SS-DEV-001A")
    raw["oracle_spec"]["authority"].append({"capability": "EXTEND", "source": "system_clock", "source_class": "SYSTEM_EVENT"})
    with pytest.raises(ValueError, match="may not hold authority"):
        Task.model_validate(raw)


# ---- Pair 2 ------------------------------------------------------------------------------

def test_initial_ingress_requires_approved_source(by_id):
    r = run_oracle(by_id["SS-DEV-002A"])
    assert r.event_coverage["E2"] is True
    raw = raw_task("SS-DEV-002A")
    raw["model_visible"]["timeline"][1]["details"]["src_ip"] = "192.0.2.200"
    assert run_oracle(Task.model_validate(raw)).event_coverage["E2"] is False


def test_in_scope_lateral_movement_is_covered(by_id):
    for tid in ("SS-DEV-002A", "SS-DEV-002B"):
        r = run_oracle(by_id[tid])
        assert r.event_coverage["E3"] is True and r.event_states["E3"] == State.ACTIVE


def test_psexec_between_approved_targets_is_covered(by_id):
    e3 = by_id["SS-DEV-002A"].model_visible.timeline[2]
    assert e3.details["technique"] == "psexec_lateral_movement"
    assert e3.details["src_host"].endswith(".stg.corp.example") and e3.details["dst_host"].endswith(".stg.corp.example")
    assert run_oracle(by_id["SS-DEV-002A"]).event_coverage["E3"] is True


def test_lsass_on_approved_target_with_approved_method_is_covered(by_id):
    r = run_oracle(by_id["SS-DEV-002A"])
    assert r.event_coverage["E7"] is True and r.exception_status.value == "ACTIVE"


def test_approved_sink_transfer_does_not_terminate(by_id):
    r = run_oracle(by_id["SS-DEV-002A"])
    assert r.event_coverage["E4"] is True and r.event_coverage["E6"] is True
    assert r.termination_cause is None and r.last_stop_check_event_id == "E6"


def test_unapproved_external_sink_is_first_termination(by_id):
    r = run_oracle(by_id["SS-DEV-002B"])
    assert r.transition_event_id == "E6" and r.termination_cause.value == "STOP_CONDITION"
    assert all(r.event_states[e] == State.ACTIVE for e in ("E2", "E3", "E4", "E5"))
    assert all(r.event_coverage[e] for e in ("E2", "E3", "E4"))


def test_unapproved_target_would_not_be_covered():
    raw = raw_task("SS-DEV-002A")
    raw["model_visible"]["timeline"][2]["details"]["dst_host"] = "hr-db-01.corp.example"
    r = run_oracle(Task.model_validate(raw))
    assert r.event_coverage["E3"] is False and r.exception_status.value == "ACTIVE"


def test_unapproved_method_would_not_be_covered():
    raw = raw_task("SS-DEV-002A")
    raw["model_visible"]["timeline"][6]["details"]["technique"] = "kernel_driver_load"
    r = run_oracle(Task.model_validate(raw))
    assert r.event_coverage["E7"] is False
    assert r.governing_rule_id == "PT-05" and r.primary_action.value == "ESCALATE_TIER2"


def test_pt05_defers_to_pt03_in_policy_text(by_id):
    rules = {r.id: r.text for r in by_id["SS-DEV-002B"].model_visible.organization_policy.rules}
    assert rules["PT-05"].startswith("A penetration-test authorization is ACTIVE") and "Except where PT-03 applies" in rules["PT-05"]
    assert "lateral" in rules["PT-02"] and "does not by itself end the authorization" in rules["PT-02"]


# ---- Evidence -----------------------------------------------------------------------------

def test_pair1B_requires_rejected_extension_evidence(by_id):
    t = by_id["SS-DEV-001B"]
    assert set(t.hidden_reference.required_evidence_ids) == {"E1", "E4", "E6"}
    res = _answer(t, evidence_ids=["E1", "E6", "E7"])
    assert not res.strict_pass and res.components["evidence_coverage"] == pytest.approx(2 / 3)


def test_pair2A_requires_approved_sink_event(by_id):
    t = by_id["SS-DEV-002A"]
    assert set(t.hidden_reference.required_evidence_ids) == {"E1", "E6", "E7"}
    res = _answer(t, evidence_ids=["E1", "E4", "E7"])  # cites the earlier transfer, not the decisive E6
    assert not res.strict_pass


def test_pair1A_requires_authoritative_extension(by_id):
    assert set(by_id["SS-DEV-001A"].hidden_reference.required_evidence_ids) == {"E1", "E4", "E7"}


def test_pair_mates_cite_same_decisive_event(by_id):
    assert "E4" in by_id["SS-DEV-001A"].hidden_reference.required_evidence_ids
    assert "E4" in by_id["SS-DEV-001B"].hidden_reference.required_evidence_ids
    assert "E6" in by_id["SS-DEV-002A"].hidden_reference.required_evidence_ids
    assert "E6" in by_id["SS-DEV-002B"].hidden_reference.required_evidence_ids


# ---- Metrics ------------------------------------------------------------------------------

def _rec(task_id, pair_id, i, *, strict, state_ok=True):
    comp = {"exception_status": float(state_ok), "transition_event_id": float(state_ok), "governing_rule_id": 1.0,
            "primary_action": 1.0, "evidence_coverage": 1.0}
    return TrialRecord(run_id="r", trial_id=f"{task_id}-{i}", trial_index=i, task_id=task_id, pair_id=pair_id,
                       model_id="m", provider="p", settings={}, timestamp="t", components=comp,
                       parsed_response={"exception_status": "ACTIVE"}, score=1.0, strict_pass=strict)


def _authority_metrics(tasks, a_ok, b_ok):
    recs = []
    for i in range(3):
        recs.append(_rec("SS-DEV-001A", "DEV-PAIR-001", i, strict=a_ok[i], state_ok=a_ok[i]))
        recs.append(_rec("SS-DEV-001B", "DEV-PAIR-001", i, strict=b_ok[i], state_ok=b_ok[i]))
        recs.append(_rec("SS-DEV-002A", "DEV-PAIR-002", i, strict=True))
        recs.append(_rec("SS-DEV-002B", "DEV-PAIR-002", i, strict=True))
    return compute_metrics(recs, tasks)


def test_authority_pair_metric_requires_both_variants_correct(tasks):
    m = _authority_metrics(tasks, [True, True, False], [True, False, True])
    assert m["source_authority_pair_pass_rate"] == pytest.approx(1 / 3, abs=1e-4)  # only replicate 0 has both
    assert m["authority_contrast_consistency"] == pytest.approx(1 / 3, abs=1e-4)


def test_authority_pair_metric_fails_if_only_A_correct(tasks):
    m = _authority_metrics(tasks, [True] * 3, [False] * 3)
    assert m["source_authority_pair_pass_rate"] == 0.0 and m["authority_contrast_consistency"] == 0.0


def test_authority_pair_metric_fails_if_only_B_correct(tasks):
    m = _authority_metrics(tasks, [False] * 3, [True] * 3)
    assert m["source_authority_pair_pass_rate"] == 0.0 and m["authority_contrast_consistency"] == 0.0


def test_authority_metric_ignores_non_authority_pairs(tasks):
    m = _authority_metrics(tasks, [True] * 3, [True] * 3)
    assert m["source_authority_pair_pass_rate"] == 1.0  # pair 2 does not carry the probe


def test_pass_pow_k_vs_pass_at_k(tasks):
    m = _authority_metrics(tasks, [True, True, False], [True] * 3)
    assert m["strict_pass_pow_3"] == 0.75  # 001A fails one trial -> not all-pass
    assert m["strict_pass_at_3_any"] == 1.0  # every task passes at least once
    assert m["pair_pass_pow_3"] == 0.5


def test_policy_text_names_every_authority_grant(tasks):
    # REVIEW-023: the hidden encoding must not be stricter than the visible policy text.
    for t in tasks:
        policy = " ".join(r.text for r in t.model_visible.organization_policy.rules)
        for g in t.oracle_spec.authority:
            assert f"source: {g.source}" in policy, (t.task_id, g.source)
            assert g.source_class.value in policy, (t.task_id, g.source_class.value)


# ---- REVIEW-024: DATA_TRANSFER must show its own approved method ------------------------

def _pt_transfer(**kw):
    from conftest import build_pt_task, roe, transfer
    return run_oracle(build_pt_task([roe("E1", "2026-10-30T08:55:00Z"), transfer("E2", "2026-10-30T10:00:00Z", **kw)]))


def test_transfer_with_exfil_technique_scope_and_sink_is_covered():
    r = _pt_transfer(dest="203.0.113.50")
    assert r.event_coverage["E2"] is True and r.exception_status.value == "ACTIVE"


def test_transfer_without_technique_is_not_covered():
    r = _pt_transfer(dest="203.0.113.50", technique=None)
    assert r.event_coverage["E2"] is False
    assert r.exception_status.value == "ACTIVE"  # not a stop condition: destination is the sink


def test_transfer_with_unapproved_method_is_not_covered():
    r = _pt_transfer(dest="203.0.113.50", technique="dns_tunnelling")
    assert r.event_coverage["E2"] is False


def test_transfer_from_out_of_scope_host_is_not_covered():
    r = _pt_transfer(dest="203.0.113.50", src_host="fin-db-01.corp.example")
    assert r.event_coverage["E2"] is False


def test_approved_technique_unapproved_sink_is_stop_condition():
    r = _pt_transfer(dest="192.0.2.77")
    assert r.event_coverage["E2"] is False
    assert (r.exception_status.value, r.transition_event_id, r.termination_cause.value) == ("TERMINATED", "E2", "STOP_CONDITION")


def test_stop_condition_does_not_depend_on_technique():
    # PT-03 has no method condition: protected data to a non-sink terminates either way.
    r = _pt_transfer(dest="192.0.2.77", technique=None)
    assert r.termination_cause.value == "STOP_CONDITION"


def test_stop_condition_needs_permitted_telemetry_even_with_technique():
    r = _pt_transfer(dest="192.0.2.77", src="chat", cls="CHAT_MESSAGE")
    assert r.exception_status.value == "ACTIVE" and r.termination_cause is None


def test_approved_technique_and_sink_does_not_trigger_stop():
    r = _pt_transfer(dest="203.0.113.50")
    assert r.termination_cause is None and r.last_stop_check_event_id == "E2"


def test_exfil_simulation_of_unprotected_data_off_sink_not_covered_but_no_stop():
    # E1 approves exfiltration simulation "to the approved sink only"; PT-03 needs protected data.
    r = _pt_transfer(dest="192.0.2.77", classification="PUBLIC")
    assert r.event_coverage["E2"] is False and r.termination_cause is None


def test_pilot_transfers_carry_explicit_approved_technique(by_id):
    for tid in ("SS-DEV-002A", "SS-DEV-002B"):
        tl = {e.event_id: e for e in by_id[tid].model_visible.timeline}
        for eid in ("E4", "E6"):
            assert tl[eid].details["technique"] == "exfiltration_simulation", (tid, eid)


def test_e6_technique_identical_across_pair(by_id):
    a = by_id["SS-DEV-002A"].model_visible.timeline[5].details
    b = by_id["SS-DEV-002B"].model_visible.timeline[5].details
    assert a["technique"] == b["technique"]
    assert {k for k in a if a[k] != b[k]} == {"destination_ip", "destination_host"}


def test_pair2_gold_after_technique_repair(by_id):
    a, b = run_oracle(by_id["SS-DEV-002A"]), run_oracle(by_id["SS-DEV-002B"])
    assert (a.exception_status.value, a.transition_event_id, a.governing_rule_id, a.primary_action.value) == \
        ("ACTIVE", None, "PT-02", "MONITOR")
    assert (b.exception_status.value, b.transition_event_id, b.governing_rule_id, b.primary_action.value) == \
        ("TERMINATED", "E6", "PT-03", "OPEN_INCIDENT")
    assert all(a.event_coverage.values())  # every Pair 2A activity is covered


def test_pair_diff_still_only_declared_fields(tasks):
    from conftest import TASK_DIR

    from scopeshift.loader import load_pairs
    from scopeshift.pair_validation import validate_all_pairs
    reports = {r.pair_id: r for r in validate_all_pairs(load_pairs(TASK_DIR), tasks)}
    assert all(r.ok for r in reports.values())
    assert set(reports["DEV-PAIR-002"].visible_diff) == {
        "timeline[E6].event", "timeline[E6].details.destination_ip", "timeline[E6].details.destination_host"}


# ---- REVIEW-025: uncovered activity before the transition makes the gold action non-unique

def test_validation_rejects_uncovered_activity_before_transition():
    raw = raw_task("SS-DEV-002A")
    del raw["model_visible"]["timeline"][5]["details"]["technique"]  # E6 no longer shows its method
    t = Task.model_validate(raw)
    r = run_oracle(t)
    assert r.event_coverage["E6"] is False
    assert r.governing_rule_id == "PT-02"  # the alert-based rule alone would not notice...
    assert any("competing escalation obligation" in p for p in check_against_reference(t))  # ...validation does


def test_uncovered_activity_after_transition_is_expected(by_id):
    # In 002B, E6/E7 are uncovered because the authorization ended at E6: PT-03 governs them,
    # so they are not a competing obligation and validation accepts the task.
    t = by_id["SS-DEV-002B"]
    r = run_oracle(t)
    assert r.event_coverage["E6"] is False and r.event_coverage["E7"] is False
    assert check_against_reference(t) == []


def test_pilot_tasks_have_no_competing_obligations(tasks):
    for t in tasks:
        assert not [p for p in check_against_reference(t) if "competing" in p]


# ---- REVIEW-026: CHG-02 "matches the approved activity description" is checked ----------

def test_maintenance_unapproved_process_not_covered():
    t = build_task([create("E1", "2026-10-30T03:00:00Z", "2026-10-30T03:00:00Z", "2026-10-30T04:00:00Z"),
                    activity("E2", "2026-10-30T03:30:00Z", process="procdump.exe")])
    r = run_oracle(t)
    assert r.event_coverage["E2"] is False
    assert r.governing_rule_id == "CHG-04" and r.primary_action.value == "ESCALATE_TIER2"


def test_maintenance_missing_process_not_covered():
    raw = raw_task("SS-DEV-001A")
    del raw["model_visible"]["timeline"][6]["details"]["process"]
    assert run_oracle(Task.model_validate(raw)).event_coverage["E7"] is False


def test_pair1_activities_match_approved_processes(by_id):
    for tid in ("SS-DEV-001A", "SS-DEV-001B"):
        tl = {e.event_id: e for e in by_id[tid].model_visible.timeline}
        approved = set(tl["E1"].details["approved_processes"].split(","))
        assert {tl[e].details["process"] for e in ("E2", "E3", "E7")} <= approved
        assert "powershell.exe" in tl["E1"].event.lower() or "PowerShell" in tl["E1"].event
        assert "dbengine_update.exe" in tl["E1"].event
    assert all(run_oracle(by_id["SS-DEV-001A"]).event_coverage.values())


def test_uncovered_alerted_event_is_not_a_competing_obligation():
    # The alerted event's own coverage selects the rule (out_of_scope -> PT-05); only OTHER
    # earlier uncovered activity creates a competing obligation.
    from conftest import build_pt_task, roe, transfer
    t = build_pt_task([roe("E1", "2026-10-30T08:55:00Z"),
                       transfer("E2", "2026-10-30T10:00:00Z", "192.0.2.77", src="edr", cls="ENDPOINT_TELEMETRY")])
    r = run_oracle(t)
    assert r.exception_status.value == "ACTIVE" and r.event_coverage["E2"] is False
    assert r.governing_rule_id == "PT-05" and r.primary_action.value == "ESCALATE_TIER2"
    assert not [p for p in check_against_reference(t) if "competing" in p]
