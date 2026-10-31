# TASK_GRADER_ALIGNMENT

Rule: the grader may only score or enforce something the model prompt explicitly asks for.
This is enforced by `scopeshift/alignment.py`, which `scripts/validate_alignment.py` and
`tests/test_alignment.py` both run. The phrases are declared in `grader.PROMPT_REQUIREMENTS`;
if any is missing from `prompts/model_under_test_v1.md`, validation fails. Line numbers refer to
`prompts/model_under_test_v1.md`. Re-run `python scripts/validate_alignment.py` for current
numbers.

| Requirement | What the grader does | Where the prompt asks for it |
|---|---|---|
| `exception_status` | 25% weight; must equal gold for strict pass | L16: `"exception_status": exactly one of "ACTIVE", "TERMINATED", "NOT_APPLICABLE"`, with definitions of each state |
| `transition_event_id` | 25% weight; exact match (incl. `null`); unknown event ID fails strict pass | L12: identify "the first event where that condition becomes satisfied", plus the end-time convention; L17: "the event_id of the first event at which the exception stopped governing … or null if exception_status is not TERMINATED" |
| `governing_rule_id` | 20% weight; exact match against one policy rule ID | L18: "the id of the single policy rule that prescribes the required action at the end of the timeline" |
| `primary_action` | 20% weight; exact enum match | L19: "exactly one of" the six enum values |
| `evidence_coverage` (`evidence_ids`) | 10% weight = fraction of required IDs cited; strict pass needs all of them; unknown IDs → 0 and strict fail; >4 IDs or duplicates → parse failure | L20: "a list of 1 to 4 distinct event_id strings from the timeline. Cite every event necessary … the event(s) that establish the authorization currently or last in force, and the transition event (if TERMINATED) or the alerted event (if not TERMINATED)" |
| `rationale_limit` | Not scored; more than 600 chars → parse failure | L21: "a brief explanation of at most 600 characters" |
| `single_json_object` | Extra text, extra fields or multiple objects → parse failure (a single whole-response ```json fence is tolerated) | L14: "Return only the required JSON object, with exactly these six fields and no others"; L23: "Do not wrap the JSON in any other text" |
| `single_value` | A list or multiple values in a scalar field → parse failure | L23: "Each field takes a single value of the stated type; lists of alternatives are not accepted" |

## Not scored (by design)

- `rationale` content. No LLM judge or similarity check is used.
- Extra *valid* evidence IDs within the cap of 4. Coverage is recall-only, so a reasonable
  analyst citing one extra relevant event is not penalized. The cap stops a model from citing
  every event.
- Confidence. None is requested and none is scored.

## Known tension (for human review)

The required evidence sets are the minimal set the oracle derives: the authorization-establishing
event(s) plus the transition event or the alerted event. The prompt describes this rule in
words (L20). A reviewer should confirm that a competent analyst would read L20 the same way.
