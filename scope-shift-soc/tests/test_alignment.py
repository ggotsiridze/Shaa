from conftest import ROOT

from scopeshift.alignment import check_alignment
from scopeshift.grader import PROMPT_REQUIREMENTS, WEIGHTS


def test_grader_fields_requested_in_prompt():
    rows, problems = check_alignment(ROOT)
    assert problems == []
    assert {r[0] for r in rows} == set(PROMPT_REQUIREMENTS)


def test_every_weighted_component_has_requirement():
    assert set(WEIGHTS) <= set(PROMPT_REQUIREMENTS)


def test_alignment_fails_if_prompt_drops_a_field():
    from scopeshift.prompt import PROMPT_PATH
    text = PROMPT_PATH.read_text()
    stripped = "\n".join(ln for ln in text.splitlines() if '"governing_rule_id"' not in ln)
    _, problems = check_alignment(ROOT, prompt_text=stripped)
    assert any("governing_rule_id" in p for p in problems)


def test_alignment_fails_if_prompt_drops_evidence_cap():
    from scopeshift.prompt import PROMPT_PATH
    text = PROMPT_PATH.read_text().replace("1 to 4 distinct", "some")
    _, problems = check_alignment(ROOT, prompt_text=text)
    assert any("evidence_coverage" in p for p in problems)
