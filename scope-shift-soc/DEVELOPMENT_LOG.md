# DEVELOPMENT_LOG

## 2026-10-31: pilot build

1. Wrote `RESEARCH_DECISION.md` first. It fixes the scope, non-claims and the 2 pilot pairs.
2. Wrote the schema (Pydantic v2, frozen, `extra="forbid"` everywhere; the answer model also uses
   `strict=True` so it never coerces types, e.g. a list into a string).
3. Wrote the oracle as a state machine that reads visible `source`/`source_class`/`details` plus
   a hidden, hand-authored `oracle_spec`. The spec encodes the policy: the authority table,
   which claim each event makes, and rule outcomes. It never reads `hidden_reference`. On its
   first run it agreed with the stored references for all 4 tasks. (Its limits are described
   precisely in v0.2 / REVIEW-015.)
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

## 2026-10-31: v0.2 repair after external adversarial review

The review found that the software was mechanically sound but that some task semantics allowed
more than one defensible answer. Repairs (details and tests in BUG_LOG.md, REVIEW-011 to REVIEW-022):

1. **Blockers.**
   - Removed the unexposed "signed" requirement from CHG-01 (REVIEW-011).
   - Rewrote PT-01/PT-02 so that initial ingress, on-target activity and target-to-target
     lateral movement are distinct and each explicitly covered (REVIEW-012).
   - While doing that, found and fixed a PT-03/PT-05 overlap (REVIEW-022) and an authority
     encoding that was stricter than the policy text (REVIEW-023).
2. **Transition model.** Added a neutral 04:00:00Z `SYSTEM_EVENT` time marker (E6) to both Pair 1
   variants. Activity moved to E7. Validation now requires every expiry transition to sit exactly
   at the window end (REVIEW-013).
3. **Source classes.** Replaced the verdict-like classes with descriptive record types
   (REVIEW-014).
4. **Oracle.** Scope is now evaluated from visible structured details: maintenance host and
   account; pentest ingress IP, target suffix, approved method, lateral-only method, sink. Added
   per-event `event_coverage`. Out-of-scope activity is reported but does not terminate. Wording
   about oracle independence was corrected everywhere (REVIEW-015).
5. **Metrics.**
   - Replaced `source_authority_accuracy` with the pair-level `source_authority_pair_pass_rate`
     and `authority_contrast_consistency` (REVIEW-016).
   - pass@k → pass^k naming, plus a separate true pass@k (REVIEW-019).
6. **Taxonomy.**
   - `HINDSIGHT_RECLASSIFICATION` is now manual-only. The automatic category is
     `EARLY_TRANSITION_ON_ACTIVE_EVENT` (REVIEW-017).
   - Found that BUG-010's citation gate no longer worked once rejected claims must be cited.
     Replaced it with a distinguishability test (REVIEW-021).
7. **Evidence.** The prompt's rule (a)/(b)/(c) and the oracle derivation now match exactly. Each
   pair's decisive event is required in both variants (REVIEW-018).
8. **Environment.** Python 3.12.3, exact pins, `pip check` clean (REVIEW-020).
9. **Mutations:** 7 new ones (M13–M19) covering the new logic. 19/19 killed.

## 2026-10-31: v0.3 repair after second external review

1. **REVIEW-024 (blocker) verified, then fixed.** Pair 2 transfers did not show their own method. E4/E6 now carry `technique: exfiltration_simulation` (identical across the pair), and transfer coverage reads it. Transfer sink rules were also aligned with the English text: the sink is required for protected data or exfiltration simulation.
2. While verifying it, found **REVIEW-025**: the gold rule only looks at the alerted event, so uncovered earlier activity (a competing PT-05/CHG-04 obligation) went unnoticed. Validation now rejects such tasks.
3. Adversarial re-review of all four tasks found **REVIEW-026**: CHG-02's "approved activity description" was not encoded. E1 gains `approved_processes`, and maintenance coverage checks `process`.
4. Docs now state explicitly that evidence is required-evidence coverage (recall), not exact-set agreement.
5. Mutations M20–M23 added; 23/23 killed.

## Design decisions made during the pilot (need author review)

- **Transition event for time-based expiry** (revised in v0.2): an identical neutral time marker
  sits exactly at the window end in both variants, and validation requires it. The v0.1
  convention ("first later event") was rejected by the review (REVIEW-013).
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
- **Oracle and tasks share an author.** The oracle checks the stored *labels* against the
  hand-authored `oracle_spec`. It cannot check that `oracle_spec` faithfully encodes the visible
  policy text. That needs human review.
- **Source authority on 001B cannot be diagnosed from a single answer.** "Trusted the comment"
  and "missed the expiry" give the same answer. Only the pair-level metrics speak to authority
  there (REVIEW-021).
