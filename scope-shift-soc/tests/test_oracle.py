from conftest import AP, INF, activity, build_task, create, extend, revoke, transfer

from scopeshift.oracle import State, run_oracle

S, E = "2026-10-30T03:00:00Z", "2026-10-30T04:00:00Z"


def test_oracle_activation():
    t = build_task([create("E1", S, S, E), activity("E2", "2026-10-30T03:10:00Z")])
    r = run_oracle(t)
    assert r.exception_status.value == "ACTIVE" and r.transition_event_id is None
    assert r.event_states == {"E1": State.ACTIVE, "E2": State.ACTIVE}
    assert r.governing_rule_id == "CHG-02" and r.primary_action.value == "DOCUMENT"


def test_oracle_pending_activation_before_window_start():
    t = build_task([create("E1", "2026-10-30T02:50:00Z", S, E), activity("E2", "2026-10-30T02:55:00Z"),
                    activity("E3", "2026-10-30T03:00:00Z")])
    r = run_oracle(t)
    assert r.event_states["E2"] == State.INACTIVE and r.event_states["E3"] == State.ACTIVE


def test_oracle_no_authorization_is_not_applicable():
    t = build_task([create("E1", S, S, E, src="ticketing", cls=INF), activity("E2", "2026-10-30T03:10:00Z")])
    r = run_oracle(t)
    assert r.exception_status.value == "NOT_APPLICABLE" and r.transition_event_id is None


def test_oracle_extension():
    t = build_task([create("E1", S, S, E), extend("E2", "2026-10-30T03:40:00Z", "2026-10-30T04:30:00Z"),
                    activity("E3", "2026-10-30T04:15:00Z")])
    r = run_oracle(t)
    assert r.exception_status.value == "ACTIVE"
    assert r.required_evidence_ids == ["E1", "E2", "E3"]


def test_oracle_expiry():
    t = build_task([create("E1", S, S, E), activity("E2", "2026-10-30T03:30:00Z"),
                    activity("E3", "2026-10-30T04:15:00Z")])
    r = run_oracle(t)
    assert r.exception_status.value == "TERMINATED" and r.transition_event_id == "E3"
    assert r.termination_cause.value == "EXPIRED"
    assert r.governing_rule_id == "CHG-04" and r.primary_action.value == "ESCALATE_TIER2"


def test_oracle_stop_condition():
    t = build_task([create("E1", S, S, E, sinks="203.0.113.50"), transfer("E2", "2026-10-30T03:10:00Z", "203.0.113.50"),
                    transfer("E3", "2026-10-30T03:20:00Z", "192.0.2.77"), activity("E4", "2026-10-30T03:30:00Z")])
    r = run_oracle(t)
    assert r.event_states["E2"] == State.ACTIVE
    assert r.exception_status.value == "TERMINATED" and r.transition_event_id == "E3"
    assert r.termination_cause.value == "STOP_CONDITION" and r.primary_action.value == "OPEN_INCIDENT"


def test_stop_condition_requires_protected_data():
    t = build_task([create("E1", S, S, E, sinks="203.0.113.50"),
                    transfer("E2", "2026-10-30T03:20:00Z", "192.0.2.77", classification="PUBLIC")])
    assert run_oracle(t).exception_status.value == "ACTIVE"


def test_stop_condition_requires_authoritative_telemetry():
    t = build_task([create("E1", S, S, E, sinks="203.0.113.50"),
                    transfer("E2", "2026-10-30T03:20:00Z", "192.0.2.77", src="chat", cls="USER_STATEMENT")])
    r = run_oracle(t)
    assert r.exception_status.value == "ACTIVE" and r.rejected_claims


def test_authoritative_extension():
    t = build_task([create("E1", S, S, E), extend("E2", "2026-10-30T03:40:00Z", "2026-10-30T04:30:00Z"),
                    activity("E3", "2026-10-30T04:29:59Z")])
    r = run_oracle(t)
    assert r.exception_status.value == "ACTIVE"
    assert r.effective_window_end.isoformat() == "2026-10-30T04:30:00+00:00"


def test_non_authoritative_extension_does_not_change_state():
    for src, cls in [("ticketing", INF), ("chat", "USER_STATEMENT"), ("email", "EXTERNAL_CONTENT"),
                     ("change_management", INF), ("ticketing", AP)]:
        t = build_task([create("E1", S, S, E), extend("E2", "2026-10-30T03:40:00Z", "2026-10-30T04:30:00Z", src, cls),
                        activity("E3", "2026-10-30T04:15:00Z")])
        r = run_oracle(t)
        assert r.exception_status.value == "TERMINATED", (src, cls)
        assert r.transition_event_id == "E3"
        assert r.rejected_claims == ["E2:AUTHORIZATION_EXTEND:unauthorized_source"]


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
    assert r.event_states["E2"] == State.ACTIVE  # stays evaluated under ACTIVE authorization
    assert r.event_states["E3"] == State.TERMINATED
    assert r.transition_event_id == "E3"


def test_non_authoritative_revocation_ignored():
    t = build_task([create("E1", S, S, E), revoke("E2", "2026-10-30T03:20:00Z", "chat", "USER_STATEMENT"),
                    activity("E3", "2026-10-30T03:30:00Z")])
    assert run_oracle(t).exception_status.value == "ACTIVE"


def test_oracle_does_not_read_hidden_reference(by_id):
    t = by_id["SS-DEV-001B"]
    lying = t.model_copy(update={"hidden_reference": by_id["SS-DEV-001A"].hidden_reference})
    assert run_oracle(lying).exception_status.value == "TERMINATED"


def test_oracle_reacts_to_visible_source_flip(by_id):
    from conftest import raw_task

    from scopeshift.oracle import check_against_reference
    from scopeshift.schema import Task

    raw = raw_task("SS-DEV-001A")
    raw["model_visible"]["timeline"][3]["source_class"] = INF
    flipped = Task.model_validate(raw)
    assert run_oracle(flipped).exception_status.value == "TERMINATED"
    assert check_against_reference(flipped)  # mismatch detected -> task validation would fail


def test_pilot_tasks_match_reference(tasks):
    from scopeshift.oracle import check_against_reference

    for t in tasks:
        assert check_against_reference(t) == []


def test_oracle_has_no_side_effects(by_id):
    t = by_id["SS-DEV-001B"]
    before = t.model_dump_json()
    run_oracle(t)
    run_oracle(t, enforce_source_authority=False)
    assert t.model_dump_json() == before
