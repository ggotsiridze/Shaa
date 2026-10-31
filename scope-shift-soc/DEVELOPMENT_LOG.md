# DEVELOPMENT_LOG

## 2026-10-31: pilot build

1. Wrote `RESEARCH_DECISION.md` first. It fixes the scope, non-claims and the 2 pilot pairs.
2. Wrote the schema (Pydantic v2, frozen, `extra="forbid"` everywhere; the answer model also uses
   `strict=True` so it never coerces types, e.g. a list into a string).
3. Wrote the oracle as a state machine that reads visible `source`/`source_class`/`details` plus
   a hidden `oracle_spec`. The spec encodes the policy: the authority table, which claim each
   event makes, and rule outcomes. It never reads `hidden_reference`. On its first run it agreed
   with the hand-written references for all 4 tasks.
4. Wrote the prompt, parser, grader, taxonomy, pair validator, alignment check, mocks, harness,
   metrics and CLI.
5. Wrote the tests. First run: 162 passed, 1 failed, which led to BUG-001.
6. Bug hunt: probed 6 suspected issues and confirmed BUG-002 to BUG-007. Fixed them and added
   regression tests.
7. Mutation testing: 12 deliberate bugs. The first run surfaced BUG-008 (stale bytecode in the
   mutation checker) and BUG-009 (M12 survived, a test gap). After the fixes, 12/12 are killed and
   the suite passes after revert.
8. Required mock runs: the taxonomy over-attributed SOURCE_AUTHORITY_CONFUSION to
   `mock-missed-transition` on 001B (BUG-010). Fixed by requiring citation of the rejected event.

## Design decisions made during the pilot (need author review)

- **Transition event for time-based expiry** = first event at or after the end time (see
  RESEARCH_DECISION §9). The alternative was an explicit "expired" CMS record, which would have
  been a second pair difference and an easy cue.
- **`governing_rule_id`** = the rule that *prescribes the action*, not the authority rule that
  explains why a claim was ignored. The prompt says this explicitly.
- **Evidence scoring** counts coverage only (recall), with a hard cap of 4 IDs, rather than
  precision. This avoids penalizing a reasonable extra citation while still blocking citation of
  every event.
- **Rule-ID format** is not enforced by the answer schema. Doing so would be an unstated
  requirement. Unknown rule IDs simply score 0.
- **`MONITOR`** for covered pentest activity and **`DOCUMENT`** for covered maintenance come from
  the policy text (PT-02 / CHG-02).
- **A whole-response Markdown fence is tolerated**; prose around the JSON is not.

## Remaining concerns (not bugs, but open)

- **Cost accounting:** cost and tokens are recorded from the completed response only. Real
  providers may bill for timed-out or 5xx attempts. A real provider adapter must report per-attempt
  usage before cost figures are trusted.
- **Latency** includes retry backoff time. Report it as wall-clock per trial, not model latency.
- **Same-timestamp events** are processed in listed order. The pilot timelines have none.
- **Pilot difficulty is unknown.** Pair 1 is a single extension; frontier models may find it easy.
  See the pivot criteria.
- **Oracle and tasks share an author.** The oracle independently checks the *labels* given the
  `oracle_spec`. It cannot check that `oracle_spec` faithfully encodes the visible policy text.
  That needs human review.
