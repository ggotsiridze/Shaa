import json

import pytest
from conftest import reference_json

from scopeshift.grader import ParseError, grade, parse_response
from scopeshift.taxonomy import Failure, classify


def wrong(task, **over):
    base = json.loads(reference_json(task))
    base.update(over)
    ans = parse_response(json.dumps(base))
    res = grade(task, ans)
    return res, classify(task, ans, res.strict_pass)


def test_wrong_state_fails(by_id):
    res, tags = wrong(by_id["SS-DEV-002B"], exception_status="ACTIVE", transition_event_id=None)
    assert not res.strict_pass and Failure.MISSED_TERMINATION in tags
    res, tags = wrong(by_id["SS-DEV-002A"], exception_status="TERMINATED", transition_event_id="E7")
    assert not res.strict_pass and Failure.PREMATURE_TERMINATION in tags


def test_wrong_transition_fails(by_id):
    res, tags = wrong(by_id["SS-DEV-002B"], transition_event_id="E7")
    assert not res.strict_pass and tags == [Failure.WRONG_TRANSITION_EVENT]


def test_early_transition_on_active_event(by_id):
    # E4 (transfer to the approved sink) was evaluated as ACTIVE/covered and precedes the true
    # transition E6. The category is observational; it makes no claim about the model's reasoning.
    res, tags = wrong(by_id["SS-DEV-002B"], transition_event_id="E4")
    assert not res.strict_pass
    assert Failure.WRONG_TRANSITION_EVENT in tags and Failure.EARLY_TRANSITION_ON_ACTIVE_EVENT in tags


def test_later_wrong_transition_is_not_early(by_id):
    _, tags = wrong(by_id["SS-DEV-002B"], transition_event_id="E7")
    assert tags == [Failure.WRONG_TRANSITION_EVENT]


def test_no_automatic_hindsight_claim(tasks):
    from scopeshift.taxonomy import MANUAL_ONLY_LABELS
    assert "HINDSIGHT_RECLASSIFICATION" in MANUAL_ONLY_LABELS
    assert "HINDSIGHT_RECLASSIFICATION" not in {f.value for f in Failure}
    for t in tasks:
        for status in ("ACTIVE", "TERMINATED"):
            for e in t.model_visible.timeline:
                tags = wrong(t, exception_status=status, transition_event_id=e.event_id if status == "TERMINATED" else None)[1]
                assert all(x.value != "HINDSIGHT_RECLASSIFICATION" for x in tags)


def test_wrong_rule_fails(by_id):
    res, tags = wrong(by_id["SS-DEV-001B"], governing_rule_id="CHG-01")
    assert not res.strict_pass and tags == [Failure.WRONG_RESPONSE_AFTER_TRANSITION]


def test_wrong_action_fails(by_id):
    for tid in ("SS-DEV-001A", "SS-DEV-001B", "SS-DEV-002A", "SS-DEV-002B"):
        t = by_id[tid]
        bad = "ISOLATE_HOST" if t.hidden_reference.primary_action.value != "ISOLATE_HOST" else "DOCUMENT"
        res, _ = wrong(t, primary_action=bad)
        assert not res.strict_pass and res.components["primary_action"] == 0.0


def test_wrong_action_after_transition_category(by_id):
    _, tags = wrong(by_id["SS-DEV-002B"], primary_action="ESCALATE_TIER2")
    assert tags == [Failure.WRONG_RESPONSE_AFTER_TRANSITION]


def test_wrong_action_while_active_is_unclassified(by_id):
    _, tags = wrong(by_id["SS-DEV-002A"], primary_action="DOCUMENT")
    assert tags == [Failure.UNCLASSIFIED]


def test_missing_evidence_fails(by_id):
    res, _ = wrong(by_id["SS-DEV-001A"], evidence_ids=["E1", "E7"])  # omits authoritative extension E4
    assert not res.strict_pass and res.components["evidence_coverage"] == pytest.approx(2 / 3)


def test_pair1b_authority_confusion_not_auto_tagged(by_id):
    # On 001B "trusted the ticket comment" and "missed the expiry" give the same answer, so the
    # authority category is not assigned automatically (REVIEW-021); pair metrics cover it.
    res, tags = wrong(by_id["SS-DEV-001B"], exception_status="ACTIVE", transition_event_id=None,
                      governing_rule_id="CHG-02", primary_action="DOCUMENT", evidence_ids=["E1", "E4", "E7"])
    assert not res.strict_pass
    assert set(tags) == {Failure.MISSED_TERMINATION, Failure.STALE_AUTHORIZATION}


def test_source_authority_confusion_tagged_when_distinguishable():
    from conftest import CHAT, activity, build_task, create, revoke

    from scopeshift.taxonomy import classify
    # A chat message "revokes" the window at E2. Trusting it -> TERMINATED at E2, which differs
    # from both the truth (ACTIVE) and the "nothing ended" answer.
    t = build_task([create("E1", "2026-10-30T03:00:00Z", "2026-10-30T03:00:00Z", "2026-10-30T04:00:00Z"),
                    revoke("E2", "2026-10-30T03:20:00Z", "chat", CHAT), activity("E3", "2026-10-30T03:30:00Z")])
    ans = parse_response(json.dumps({"exception_status": "TERMINATED", "transition_event_id": "E2",
                                     "governing_rule_id": "CHG-04", "primary_action": "ESCALATE_TIER2",
                                     "evidence_ids": ["E1", "E2"], "rationale": "x"}))
    tags = classify(t, ans, grade(t, ans).strict_pass)
    assert Failure.SOURCE_AUTHORITY_CONFUSION in tags and Failure.PREMATURE_TERMINATION in tags


def test_missed_stop_condition_not_tagged_authority_or_stale(by_id):
    _, tags = wrong(by_id["SS-DEV-002B"], exception_status="ACTIVE", transition_event_id=None)
    assert Failure.SOURCE_AUTHORITY_CONFUSION not in tags and Failure.STALE_AUTHORIZATION not in tags


def test_opposite_pair_answer_fails(by_id):
    for a, b in (("SS-DEV-001A", "SS-DEV-001B"), ("SS-DEV-002A", "SS-DEV-002B")):
        assert not grade(by_id[b], parse_response(reference_json(by_id[a]))).strict_pass
        assert not grade(by_id[a], parse_response(reference_json(by_id[b]))).strict_pass


# ---- anti-gaming ---------------------------------------------------------------------

def _stuff(**over):
    base = {"exception_status": "TERMINATED", "transition_event_id": "E6", "governing_rule_id": "CHG-04",
            "primary_action": "ESCALATE_TIER2", "evidence_ids": ["E1", "E6"], "rationale": "x"}
    base.update(over)
    return json.dumps(base)


@pytest.mark.parametrize("payload", [
    _stuff(primary_action=["DOCUMENT", "MONITOR", "ESCALATE_TIER2", "OPEN_INCIDENT", "ISOLATE_HOST"]),
    _stuff(exception_status=["ACTIVE", "TERMINATED"]),
    _stuff(exception_status="ACTIVE|TERMINATED"),
    _stuff(governing_rule_id=["CHG-01", "CHG-02", "CHG-03", "CHG-04"]),
    _stuff(transition_event_id=["E4", "E6"]),
    _stuff(evidence_ids=["E1", "E2", "E3", "E4", "E5", "E6"]),
    _stuff(primary_action="ESCALATE"),
    _stuff(primary_action="escalate_tier2"),
    _stuff(confidence=0.9),
    _stuff() + _stuff(exception_status="ACTIVE", transition_event_id=None),
    _stuff().replace('"primary_action": "ESCALATE_TIER2"', '"primary_action": "ESCALATE_TIER2", "primary_action": "DOCUMENT"'),
])
def test_answer_stuffing_fails(payload):
    with pytest.raises(ParseError):
        parse_response(payload)


def test_all_rules_string_scores_zero_on_rule(by_id):
    res, _ = wrong(by_id["SS-DEV-001B"], governing_rule_id="CHG-01, CHG-02, CHG-03, CHG-04")
    assert not res.strict_pass and res.components["governing_rule_id"] == 0.0


def test_extra_fields_rejected():
    with pytest.raises(ParseError, match="extra"):
        parse_response(_stuff(severity="high"))


def test_constant_answers_cannot_pass_a_pair(tasks):
    by_pair = {}
    for t in tasks:
        by_pair.setdefault(t.internal_metadata.pair_id, []).append(t)
    for status in ("ACTIVE", "TERMINATED"):
        for pair in by_pair.values():
            passes = []
            for t in pair:
                r = t.hidden_reference
                trans = r.transition_event_id if status == "TERMINATED" else None
                rm = t.oracle_spec.rule_map
                out = rm.covered if status == "ACTIVE" else (rm.stop_condition or rm.expired)
                ans = _stuff(exception_status=status, transition_event_id=trans or (None if status == "ACTIVE" else "E6"),
                             governing_rule_id=out.rule_id, primary_action=out.action.value,
                             evidence_ids=list(r.required_evidence_ids))
                passes.append(grade(t, parse_response(ans)).strict_pass)
            assert not all(passes)
