import json

import pytest
from conftest import ROOT

from scopeshift.prompt import build_prompt, load_template

FORBIDDEN = [
    "hidden_reference", "oracle_spec", "internal_metadata", "pair_id", "DEV-PAIR", "variant",
    "Variant", "gold", "Gold", "expected_action", "expected_transition", "required_evidence_ids",
    "rule_map", "event_semantics", "probes", "human_review_status", "SS-DEV", "benchmark",
    "hypothesis", "opposite", "oracle",
]


def full_prompt(task) -> str:
    p = build_prompt(task)
    return p.system + "\n" + p.user


@pytest.mark.parametrize("token", FORBIDDEN)
def test_forbidden_tokens_absent(tasks, token):
    for t in tasks:
        assert token not in full_prompt(t), (t.task_id, token)


def test_hidden_reference_not_in_prompt(tasks):
    for t in tasks:
        text = full_prompt(t)
        assert "hidden_reference" not in text
        ref = t.hidden_reference.model_dump(mode="json")
        assert json.dumps(ref) not in text
        assert '"required_evidence_ids"' not in text


def test_oracle_not_in_prompt(tasks):
    for t in tasks:
        text = full_prompt(t)
        assert "oracle_spec" not in text and "AUTHORIZATION_EXTEND" not in text and "SCOPE_ACTIVITY" not in text


def test_pair_id_not_in_prompt(tasks):
    for t in tasks:
        assert t.internal_metadata.pair_id not in full_prompt(t)


def test_variant_not_in_prompt(tasks):
    for t in tasks:
        text = full_prompt(t)
        assert "variant" not in text.lower()
        assert t.task_id not in text  # task ids end with the variant letter


def test_prompt_contains_only_model_visible(tasks):
    for t in tasks:
        user = build_prompt(t).user
        start = user.index("{")
        assert json.loads(user[start:]) == t.model_visible.model_dump(mode="json")


def test_template_has_no_task_specific_content():
    system, user = load_template()
    for word in ("CHG-104", "PT-2026", "app-db-02", "E6", "E7"):
        assert word not in system + user


def test_prompt_does_not_request_chain_of_thought():
    text = (ROOT / "prompts" / "model_under_test_v1.md").read_text().lower()
    for phrase in ("step by step", "chain of thought", "think aloud", "show your reasoning"):
        assert phrase not in text
