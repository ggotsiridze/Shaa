import json

import pytest
from conftest import answer_json

from scopeshift.grader import ParseError, parse_response


def test_invalid_json():
    with pytest.raises(ParseError, match="invalid JSON"):
        parse_response("{not json}")


def test_empty_response():
    for raw in ("", "   \n\t "):
        with pytest.raises(ParseError, match="empty"):
            parse_response(raw)


def test_none_response():
    with pytest.raises(ParseError):
        parse_response(None)


def test_null_json():
    with pytest.raises(ParseError, match="not object"):
        parse_response("null")


def test_leading_trailing_whitespace():
    assert parse_response("\n\n  " + answer_json() + "  \n").exception_status == "ACTIVE"


def test_key_order_irrelevant():
    d = json.loads(answer_json())
    assert parse_response(json.dumps(dict(reversed(list(d.items()))))) == parse_response(answer_json())


def test_markdown_fence_whole_response_only():
    assert parse_response("```json\n" + answer_json() + "\n```").primary_action == "DOCUMENT"
    assert parse_response("```\n" + answer_json() + "\n```").primary_action == "DOCUMENT"
    with pytest.raises(ParseError):
        parse_response("Here you go:\n```json\n" + answer_json() + "\n```")
    with pytest.raises(ParseError):
        parse_response("```json\n" + answer_json() + "\n```\nHope this helps")


def test_prose_around_json_rejected():
    with pytest.raises(ParseError):
        parse_response("Answer: " + answer_json())


def test_truncated_json():
    full = answer_json()
    for cut in (1, len(full) // 2, len(full) - 1):
        with pytest.raises(ParseError):
            parse_response(full[:cut])


def test_wrong_field_type():
    for over in ({"evidence_ids": "E1"}, {"evidence_ids": [1, 2]}, {"transition_event_id": 6},
                 {"exception_status": True}, {"rationale": None}, {"governing_rule_id": 2}):
        with pytest.raises(ParseError):
            parse_response(answer_json(**over))


def test_missing_field():
    d = json.loads(answer_json())
    del d["transition_event_id"]  # must be explicit null, not omitted
    with pytest.raises(ParseError):
        parse_response(json.dumps(d))


def test_very_long_rationale():
    assert parse_response(answer_json(rationale="a" * 600))
    with pytest.raises(ParseError):
        parse_response(answer_json(rationale="a" * 601))
    with pytest.raises(ParseError):
        parse_response(answer_json(rationale="a" * 1_000_000))


def test_unknown_fields():
    with pytest.raises(ParseError):
        parse_response(answer_json(reasoning="step by step"))


def test_duplicate_evidence_ids():
    with pytest.raises(ParseError):
        parse_response(answer_json(evidence_ids=["E1", "E1"]))


def test_empty_evidence_and_bad_ids():
    for ev in ([], ["e1"], ["E01"], ["E0"], [" E1"], ["E1 "]):
        with pytest.raises(ParseError):
            parse_response(answer_json(evidence_ids=ev))


def test_deeply_nested_json_fails_safely():
    with pytest.raises(ParseError):
        parse_response("[" * 100_000 + "]" * 100_000)


def test_nan_rejected_by_types():
    with pytest.raises(ParseError):
        parse_response(answer_json().replace('"rationale": "x"', '"rationale": NaN'))
