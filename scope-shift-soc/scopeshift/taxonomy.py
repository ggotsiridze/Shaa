"""Failure taxonomy. Only assigns a category when it can be determined from the answer alone.

All automatic categories are OBSERVATIONAL: they describe what the answer did, not why.
Unclassifiable strict failures get `UNCLASSIFIED` rather than a guessed label.

`HINDSIGHT_RECLASSIFICATION` is deliberately NOT an automatic category (REVIEW-017). It may be
assigned only by a human during transcript analysis, when the written rationale shows the model
used later information to re-judge earlier, then-authorized activity. See MANUAL_ONLY_LABELS.
"""

from __future__ import annotations

from enum import Enum

from scopeshift.oracle import State, run_oracle
from scopeshift.schema import ModelAnswer, Task, TerminationCause


class Failure(str, Enum):
    SOURCE_AUTHORITY_CONFUSION = "SOURCE_AUTHORITY_CONFUSION"
    STALE_AUTHORIZATION = "STALE_AUTHORIZATION"
    PREMATURE_TERMINATION = "PREMATURE_TERMINATION"
    MISSED_TERMINATION = "MISSED_TERMINATION"
    WRONG_TRANSITION_EVENT = "WRONG_TRANSITION_EVENT"
    WRONG_RESPONSE_AFTER_TRANSITION = "WRONG_RESPONSE_AFTER_TRANSITION"
    EARLY_TRANSITION_ON_ACTIVE_EVENT = "EARLY_TRANSITION_ON_ACTIVE_EVENT"
    PARSE_FAILURE = "PARSE_FAILURE"
    INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"
    UNCLASSIFIED = "UNCLASSIFIED"


# Labels a human reviewer may add from the rationale; never produced by classify().
MANUAL_ONLY_LABELS = ("HINDSIGHT_RECLASSIFICATION",)


def classify(task: Task, answer: ModelAnswer, strict_pass: bool) -> list[Failure]:
    if strict_pass:
        return []
    ref = task.hidden_reference
    truth = run_oracle(task)
    tags: list[Failure] = []
    ref_status, got = ref.exception_status.value, answer.exception_status

    # Source-authority confusion: the answer's (status, transition) equals what the oracle derives
    # when every claimed state change is accepted regardless of source, that differs from the truth,
    # AND it differs from the "nothing ever ended" answer (ACTIVE, null). When the authority-blind
    # answer IS (ACTIVE, null) -- e.g. SS-DEV-001B -- trusting the bad source and simply missing the
    # expiry produce identical answers, so the category is not reliably determinable and is not
    # assigned (BUG-010, REVIEW-021). Authority is then measured by the pair-level metrics.
    naive = run_oracle(task, enforce_source_authority=False)
    naive_key = (naive.exception_status.value, naive.transition_event_id)
    distinguishable = naive_key != ("ACTIVE", None)
    if (truth.rejected_claims and distinguishable
            and naive_key != (truth.exception_status.value, truth.transition_event_id)
            and (got, answer.transition_event_id) == naive_key):
        tags.append(Failure.SOURCE_AUTHORITY_CONFUSION)

    if ref_status == "TERMINATED" and got == "ACTIVE":
        tags.append(Failure.MISSED_TERMINATION)
        if truth.termination_cause in (TerminationCause.EXPIRED, TerminationCause.REVOKED):
            tags.append(Failure.STALE_AUTHORIZATION)
    elif ref_status == "ACTIVE" and got == "TERMINATED":
        tags.append(Failure.PREMATURE_TERMINATION)
    elif ref_status == "TERMINATED" and got == "TERMINATED":
        if answer.transition_event_id != ref.transition_event_id:
            tags.append(Failure.WRONG_TRANSITION_EVENT)
            if _is_early_transition_on_active_event(task, answer, truth.event_states):
                tags.append(Failure.EARLY_TRANSITION_ON_ACTIVE_EVENT)
        elif answer.primary_action != ref.primary_action.value or answer.governing_rule_id != ref.governing_rule_id:
            tags.append(Failure.WRONG_RESPONSE_AFTER_TRANSITION)

    return tags or [Failure.UNCLASSIFIED]


def _is_early_transition_on_active_event(task: Task, answer: ModelAnswer, event_states: dict[str, State]) -> bool:
    """The selected transition event occurred before the true transition and was evaluated as
    ACTIVE (covered) by the oracle. Observational only: says nothing about the model's reasoning."""
    ids = [e.event_id for e in task.model_visible.timeline]
    chosen, true_t = answer.transition_event_id, task.hidden_reference.transition_event_id
    if chosen not in ids or true_t is None:
        return False
    return ids.index(chosen) < ids.index(true_t) and event_states.get(chosen) == State.ACTIVE
