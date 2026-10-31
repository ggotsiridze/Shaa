# TASK_GRADER_ALIGNMENT

Rule: the grader may only score or enforce something the model prompt explicitly asks for.
This is enforced by `scopeshift/alignment.py`, which `scripts/validate_alignment.py` and
`tests/test_alignment.py` both run. The phrases are declared in `grader.PROMPT_REQUIREMENTS`;
if any is missing from the active prompt `prompts/model_under_test_v2.md`, validation fails. Line numbers refer to
`prompts/model_under_test_v2.md`. (The coached pilot prompt v1 is archived in `prompts/archive/`.) Re-run `python scripts/validate_alignment.py` for current
numbers.

| Requirement | What the grader does | Where the prompt asks for it |
|---|---|---|
| `exception_status` | 25% weight; must equal gold for strict pass | L8: `"exception_status": exactly one of "ACTIVE", "TERMINATED", "NOT_APPLICABLE"`, with definitions of each state |
| `transition_event_id` | 25% weight; exact match (incl. `null`); unknown event ID fails strict pass | L9: "the event_id of the first event at which the exception no longer governed (if it ended because its end time was reached, the first timeline event at or after that time), or null if exception_status is not TERMINATED" |
| `governing_rule_id` | 20% weight; exact match against one policy rule ID | L10: "the id of the single policy rule that prescribes the required action at the end of the timeline" |
| `primary_action` | 20% weight; exact enum match | L11: "exactly one of" the six enum values |
| `evidence_coverage` (`evidence_ids`) | 10% weight = fraction of required IDs cited; strict pass needs all of them; unknown IDs → 0 and strict fail; >4 IDs or duplicates → parse failure | L12: "a list of 1 to 4 distinct event_id strings from the timeline. Cite every event necessary … (a) the event(s) that created the authorization and any permitted change to it; (b) any event that claimed to create, extend, modify or revoke the authorization but did not take effect; (c) if exception_status is TERMINATED, the transition event; otherwise, the alerted event and, if the policy defines a stop condition, the most recent event that was evaluated against that stop condition" |
| `rationale_limit` | Not scored; more than 2000 chars → parse failure (raised from 600 after the pilot: Haiku failed only on length) | L13: "a brief explanation of at most 2000 characters" |
| `single_json_object` | Extra text, extra fields or multiple objects → parse failure (a single whole-response ```json fence is tolerated) | L6: "Return only the required JSON object, with exactly these six fields and no others"; L15: "Do not wrap the JSON in any other text" |
| `single_value` | A list or multiple values in a scalar field → parse failure | L15: "Each field takes a single value of the stated type; lists of alternatives are not accepted" |

## Not scored (by design)

- `rationale` content. No LLM judge or similarity check is used.
- Extra *valid* evidence IDs within the cap of 4. Coverage is recall-only, so a reasonable
  analyst citing one extra relevant event is not penalized. The cap stops a model from citing
  every event.
- Confidence. None is requested and none is scored.

## Known tension (for human review)

Evidence is scored as **required-evidence coverage (recall)**, not exact-set agreement: every
required ID must be cited for strict pass, and extra valid IDs (within the cap of 4) are not
penalized. The required IDs are exactly what the oracle derives from rule (a)/(b)/(c) on L12
(REVIEW-018): 001A {E1,E4,E7}, 001B {E1,E4,E6}, 002A {E1,E6,E7}, 002B {E1,E6}. Each pair's
decisive event (E4 / E6) is required in both variants. A reviewer should confirm that a
competent analyst would read L20 the same way.
