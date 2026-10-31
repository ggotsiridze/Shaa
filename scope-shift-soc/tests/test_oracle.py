from conftest import (
    CHAT,
    TR,
    activity,
    build_pt_task,
    build_task,
    create,
    extend,
    marker,
    pt_ingress,
    pt_lateral,
    pt_on_host,
    raw_task,
    revoke,
    roe,
    transfer,
)

from scopeshift.oracle import State, check_against_reference, run_oracle
from scopeshift.schema import Task

S, E = "2026-10-30T03:00:00Z", "2026-10-30T04:00:00Z"
SINK, BAD = "203.0.113.50", "192.0.2.77"


# ---- lifecycle --------------------------------------------------------------------------

def test_oracle_activation():
    t = build_task([create("E1", S, S, E), activity("E2", "2026-10-30T03:10:00Z")])
    r = run_oracle(t)
    assert r.exception_status.value == "ACTIVE" and r.transition_event_id is None
    assert r.event_states == {"E1": State.ACTIVE, "E2": State.ACTIVE}
    assert r.event_coverage == {"E2": True}
    assert r.governing_rule_id == "CHG-02" and r.primary_action.value == "DOCUMENT"


def test_oracle_pending_activation_before_window_start():
    t = build_task([create("E1", "2026-10-30T02:50:00Z", S, E), activity("E2", "2026-10-30T02:55:00Z"),
                    activity("E3", "2026-10-30T03:00:00Z")])
    r = run_oracle(t)
    assert r.event_states["E2"] == State.INACTIVE and r.event_states["E3"] == State.ACTIVE
    assert r.event_coverage == {"E2": False, "E3": True}


def test_oracle_no_authorization_is_not_applicable():
    t = build_task([create("E1", S, S, E, src="ticketing", cls=TR), activity("E2", "2026-10-30T03:10:00Z")])
    r = run_oracle(t)
    assert r.exception_status.value == "NOT_APPLICABLE" and r.transition_event_id is None


def test_oracle_extension():
    t = build_task([create("E1", S, S, E), extend("E2", "2026-10-30T03:40:00Z", "2026-10-30T04:30:00Z"),
                    marker("E3", E), activity("E4", "2026-10-30T04:15:00Z")])
    r = run_oracle(t)
    assert r.exception_status.value == "ACTIVE"
    assert r.required_evidence_ids == ["E1", "E2", "E4"]


def test_oracle_expiry():
    t = build_task([create("E1", S, S, E), activity("E2", "2026-10-30T03:30:00Z"), marker("E3", E),
                    activity("E4", "2026-10-30T04:15:00Z")])
    r = run_oracle(t)
    assert r.exception_status.value == "TERMINATED" and r.transition_event_id == "E3"
    assert r.termination_cause.value == "EXPIRED"
    assert r.governing_rule_id == "CHG-04" and r.primary_action.value == "ESCALATE_TIER2"
    assert r.event_coverage == {"E2": True, "E4": False}


def test_maintenance_activity_on_other_host_not_covered():
    t = build_task([create("E1", S, S, E), activity("E2", "2026-10-30T03:30:00Z", host="app-web-09.corp.example")])
    r = run_oracle(t)
    assert r.exception_status.value == "ACTIVE" and r.event_coverage["E2"] is False
    assert r.governing_rule_id == "CHG-04" and r.primary_action.value == "ESCALATE_TIER2"


def test_maintenance_activity_by_other_account_not_covered():
    t = build_task([create("E1", S, S, E), activity("E2", "2026-10-30T03:30:00Z", account="jdoe")])
    assert run_oracle(t).event_coverage["E2"] is False


def test_authoritative_extension():
    t = build_task([create("E1", S, S, E), extend("E2", "2026-10-30T03:40:00Z", "2026-10-30T04:30:00Z"),
                    activity("E3", "2026-10-30T04:29:59Z")])
    r = run_oracle(t)
    assert r.exception_status.value == "ACTIVE"
    assert r.effective_window_end.isoformat() == "2026-10-30T04:30:00+00:00"


def test_non_authoritative_extension_does_not_change_state():
    for src, cls in [("ticketing", TR), ("chat", CHAT), ("email", "EMAIL_MESSAGE"),
                     ("change_management", TR), ("ticketing", "CHANGE_RECORD")]:
        t = build_task([create("E1", S, S, E), extend("E2", "2026-10-30T03:40:00Z", "2026-10-30T04:30:00Z", src, cls),
                        marker("E3", E), activity("E4", "2026-10-30T04:15:00Z")])
        r = run_oracle(t)
        assert r.exception_status.value == "TERMINATED", (src, cls)
        assert r.transition_event_id == "E3"
        assert r.rejected_claims == ["E2:AUTHORIZATION_EXTEND:unauthorized_source"]
        assert r.rejected_claim_event_ids == ["E2"]
        assert r.required_evidence_ids == ["E1", "E2", "E3"]


def test_extension_after_expiry_does_not_revive():
    t = build_task([create("E1", S, S, E), extend("E2", "2026-10-30T04:05:00Z", "2026-10-30T04:30:00Z"),
                    activity("E3", "2026-10-30T04:15:00Z")])
    r = run_oracle(t)
    assert r.exception_status.value == "TERMINATED" and r.transition_event_id == "E2"


def test_shortening_extension_ignored():
    t = build_task([create("E1", S, S, E), extend("E2", "2026-10-30T03:10:00Z", "2026-10-30T03:20:00Z"),
                    activity("E3", "2026-10-30T03:30:00Z")])
    assert run_oracle(t).exception_status.value == "ACTIVE"


def test_first_transition_recorded():
    t = build_task([create("E1", S, S, E), revoke("E2", "2026-10-30T03:20:00Z"),
                    revoke("E3", "2026-10-30T03:30:00Z"), activity("E4", "2026-10-30T04:15:00Z")])
    r = run_oracle(t)
    assert r.transition_event_id == "E2" and r.termination_cause.value == "REVOKED"


def test_no_retroactive_revocation():
    t = build_task([create("E1", S, S, E), activity("E2", "2026-10-30T03:10:00Z"),
                    revoke("E3", "2026-10-30T03:20:00Z"), activity("E4", "2026-10-30T03:30:00Z")])
    r = run_oracle(t)
    assert r.event_states["E2"] == State.ACTIVE and r.event_coverage["E2"] is True
    assert r.event_states["E3"] == State.TERMINATED and r.event_coverage["E4"] is False
    assert r.transition_event_id == "E3"


def test_non_authoritative_revocation_ignored():
    t = build_task([create("E1", S, S, E), revoke("E2", "2026-10-30T03:20:00Z", "chat", CHAT),
                    activity("E3", "2026-10-30T03:30:00Z")])
    assert run_oracle(t).exception_status.value == "ACTIVE"


def test_time_marker_cannot_change_state():
    # A system clock event claiming an extension is rejected; the marker kind itself is inert.
    t = build_task([create("E1", S, S, E), extend("E2", "2026-10-30T03:40:00Z", "2026-10-30T04:30:00Z", "system_clock", "SYSTEM_EVENT"),
                    marker("E3", E), activity("E4", "2026-10-30T04:15:00Z")])
    r = run_oracle(t)
    assert r.transition_event_id == "E3" and r.rejected_claim_event_ids == ["E2"]
    t2 = build_task([create("E1", S, S, E), marker("E2", "2026-10-30T03:30:00Z"), activity("E3", "2026-10-30T03:40:00Z")])
    assert run_oracle(t2).exception_status.value == "ACTIVE"


# ---- pentest scope (structured visible details) ---------------------------------------

def _pt(*events):
    return run_oracle(build_pt_task([roe("E1", "2026-10-30T08:55:00Z"), *events]))


def test_pentest_stop_condition():
    r = _pt(transfer("E2", "2026-10-30T10:10:00Z", SINK), transfer("E3", "2026-10-30T10:20:00Z", BAD),
            pt_on_host("E4", "2026-10-30T10:30:00Z"))
    assert r.event_states["E2"] == State.ACTIVE and r.event_coverage["E2"] is True
    assert r.exception_status.value == "TERMINATED" and r.transition_event_id == "E3"
    assert r.termination_cause.value == "STOP_CONDITION" and r.primary_action.value == "OPEN_INCIDENT"
    assert r.event_coverage["E3"] is False and r.event_coverage["E4"] is False


def test_stop_condition_requires_protected_data():
    r = _pt(transfer("E2", "2026-10-30T10:20:00Z", BAD, classification="PUBLIC"))
    assert r.exception_status.value == "ACTIVE"
    assert r.event_coverage["E2"] is False  # still not covered: destination is not the approved sink


def test_stop_condition_requires_permitted_telemetry():
    r = _pt(transfer("E2", "2026-10-30T10:20:00Z", BAD, src="chat", cls=CHAT))
    assert r.exception_status.value == "ACTIVE" and r.rejected_claims


def test_unapproved_target_lateral_movement_not_covered():
    r = _pt(pt_lateral("E2", "2026-10-30T10:00:00Z", dst_host="hr-db-01.corp.example"))
    assert r.event_coverage["E2"] is False
    assert r.exception_status.value == "ACTIVE"  # out of scope does not by itself terminate
    assert r.governing_rule_id == "PT-05" and r.primary_action.value == "ESCALATE_TIER2"


def test_lateral_movement_from_unapproved_source_host_not_covered():
    r = _pt(pt_lateral("E2", "2026-10-30T10:00:00Z", src_host="laptop-17.corp.example"))
    assert r.event_coverage["E2"] is False


def test_unapproved_method_not_covered():
    r = _pt(pt_on_host("E2", "2026-10-30T10:00:00Z", technique="ransomware_encryption"))
    assert r.event_coverage["E2"] is False


def test_non_lateral_method_used_for_lateral_movement_not_covered():
    r = _pt(pt_lateral("E2", "2026-10-30T10:00:00Z", technique="lsass_credential_dumping"))
    assert r.event_coverage["E2"] is False


def test_external_initial_source_not_covered():
    r = _pt(pt_ingress("E2", "2026-10-30T09:30:00Z", src_ip="192.0.2.200"))
    assert r.event_coverage["E2"] is False


def test_ingress_to_out_of_scope_target_not_covered():
    r = _pt(pt_ingress("E2", "2026-10-30T09:30:00Z", dst_host="mail.corp.example"))
    assert r.event_coverage["E2"] is False


def test_lsass_not_covered_when_credential_dumping_not_approved():
    t = build_pt_task([roe("E1", "2026-10-30T08:55:00Z", methods="password_spraying,psexec_lateral_movement"),
                       pt_on_host("E2", "2026-10-30T10:00:00Z")])
    assert run_oracle(t).event_coverage["E2"] is False


def test_target_suffix_match_is_not_substring():
    r = _pt(pt_on_host("E2", "2026-10-30T10:00:00Z", host="evil-stg.corp.example.attacker.example"))
    assert r.event_coverage["E2"] is False


def test_activity_outside_window_not_covered():
    r = _pt(pt_on_host("E2", "2026-10-30T08:58:00Z"))
    assert r.event_coverage["E2"] is False and r.exception_status.value == "NOT_APPLICABLE"


# ---- independence / consistency checks -------------------------------------------------

def test_oracle_does_not_read_hidden_reference(by_id):
    t = by_id["SS-DEV-001B"]
    lying = t.model_copy(update={"hidden_reference": by_id["SS-DEV-001A"].hidden_reference})
    assert run_oracle(lying).exception_status.value == "TERMINATED"


def test_oracle_reacts_to_visible_source_flip():
    raw = raw_task("SS-DEV-001A")
    raw["model_visible"]["timeline"][3]["source"] = "ticketing"
    raw["model_visible"]["timeline"][3]["source_class"] = TR
    flipped = Task.model_validate(raw)
    assert run_oracle(flipped).exception_status.value == "TERMINATED"
    assert check_against_reference(flipped)  # mismatch detected -> task validation would fail


def test_oracle_reacts_to_visible_destination_flip():
    raw = raw_task("SS-DEV-002A")
    raw["model_visible"]["timeline"][5]["details"]["destination_ip"] = BAD
    flipped = Task.model_validate(raw)
    r = run_oracle(flipped)
    assert (r.exception_status.value, r.transition_event_id) == ("TERMINATED", "E6")
    assert check_against_reference(flipped)


def test_pilot_tasks_match_reference(tasks):
    for t in tasks:
        assert check_against_reference(t) == []


def test_oracle_has_no_side_effects(by_id):
    t = by_id["SS-DEV-001B"]
    before = t.model_dump_json()
    run_oracle(t)
    run_oracle(t, enforce_source_authority=False)
    assert t.model_dump_json() == before
