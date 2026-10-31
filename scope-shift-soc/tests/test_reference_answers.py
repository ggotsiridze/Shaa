from conftest import reference_json

from scopeshift.grader import grade, parse_response
from scopeshift.taxonomy import classify


def test_reference_answer_passes(tasks):
    for t in tasks:
        r = grade(t, parse_response(reference_json(t)))
        assert r.strict_pass and r.score == 1.0, t.task_id
        assert classify(t, parse_response(reference_json(t)), True) == []


def test_reference_answer_fenced_passes(tasks):
    for t in tasks:
        assert grade(t, parse_response("```json\n" + reference_json(t) + "\n```")).strict_pass
