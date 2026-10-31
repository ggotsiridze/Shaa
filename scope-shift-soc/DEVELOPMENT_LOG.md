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

## 2026-10-31: first real-model pilot -> harder tasks (v0.4)

**Pilot results:** prompt v1, `tasks/development`, 5 trials per task. These are real API runs; results and transcripts are committed.

| Model | Strict pass | pass^5 | Real reasoning errors |
|---|---|---|---|
| claude-opus-5-5 | 100% | 100% | 0 |
| claude-sonnet-5-5 | 100% | 100% | 0 |
| claude-haiku-5-5 | 85% (100% after re-grade) | 50% (100%) | 0 |

**Haiku's 3 failures were only the rationale length.** It wrote about 790 characters against a 600-character limit, while every scored field was correct. The rationale is not scored, so the limit was measuring wordiness, not the capability.

**Changes in v0.4:**
1. **Rationale limit 600 → 2000.** `scripts/regrade.py` re-scored the saved raw answers with no API calls; Haiku goes to 100%. The re-grade output notes that the model was instructed with the 600 limit.
2. **No headroom on the pilot tasks.** This meets the pivot criterion in RESEARCH_DECISION §16.
   - **Prompt v2** removes the strategy coaching but keeps every field definition the grader needs. v1 is archived for reproducibility. `--prompt-version` selects the prompt, and runs record it.
   - **`tasks/pilot2`:** 8 harder candidate tasks (4 controlled pairs).
     - **Longer timelines:** 11–15 events.
     - **Neutral SIEM checkpoints:** every 30 or 60 minutes, so the expiry boundary isn't singled out.
     - **Noise:** change/register notes that change nothing, chat, monitoring.
     - **Traps:**
       - chained extensions, valid then ticket-only;
       - a backdated revocation, "effective 23:10", recorded at 23:41, across midnight;
       - a revocation by email;
       - a protected-data transfer reported by EDR, which cannot establish the stop condition, vs DLP;
       - an approval window in CET (UTC+01:00) vs UTC.
3. **Validator refinement:** the alerted event is exempt from the competing-obligation rule, because its own coverage selects the rule (needed for 005A).
4. **Pair validator caught an issue:** the alert `source` differed in pair 005, an undeclared cue. Fixed: the alert comes from SIEM in both variants.

**Gold answers for `tasks/pilot2` are still PENDING human review.**

## 2026-10-31: Anthropic (Claude) provider, no benchmark changes

- Added `scopeshift/providers/anthropic_provider.py`, built on the official SDK (`anthropic==2.3.0`, pinned) and the Messages API. The system prompt goes in `system` and the incident JSON in a single user message. Optional `output_config.format` uses the same schema as OpenAI. `output_config.effort` is sent only when given; `thinking` is never sent. `max_tokens` defaults to 16000. `--temperature` is rejected up front.
- **Validity choices:**
  - Refusal fallbacks are OFF, so another model can never answer.
  - The base URL is pinned. This cloud session sets an internal `ANTHROPIC_BASE_URL`, which the SDK would otherwise follow.
  - SDK retries are OFF.
  - Refusals and `max_tokens` truncation are model output.
- **Errors:** 429/500/529 are transient. 401/403 → AUTH, 402 → QUOTA, 400/404/413 → BAD_REQUEST.
- **Tests:** 29 tests that run the real SDK over an in-memory transport, with no network or cost.
- **Mutations:** M26 (base URL not pinned) and M27 (refusal treated as infrastructure).
- **CLI:** settings validation now runs before `--dry-run`, and the plan shows the provider's own key variable.
- **Unchanged:** tasks, prompt, gold answers, oracle, grader, weights, pairs, taxonomy, metrics.
- **Not yet done:** a real paid call. `ANTHROPIC_API_KEY` is not set in this session, though `api.anthropic.com` is reachable.

## 2026-10-31: real-model support (OpenAI provider), no benchmark changes

- Added `scopeshift/providers/openai_provider.py`, built on the official SDK (`openai==3.42.0`, pinned) and the Responses API. It sends `prompt.system` as `instructions` and `prompt.user` as `input`. An optional strict JSON schema restates only the field set, types and enums. `store=false`. SDK internal retries are disabled, so the harness counts every attempt.
- Errors are mapped to the existing infrastructure taxonomy. Added the non-retryable kinds QUOTA and BAD_REQUEST. API keys are redacted from all error text.
- The harness records extra audit fields: total tokens, response ID, returned model, status, provider response dump, retry count and prompt hashes. Retries have optional jitter (default 0, so mocks are unchanged). Records are written incrementally.
- `run_eval.py` gains `--provider openai`, `--task-ids`, `--dry-run` (zero calls), `--validate-api` (model metadata only), a plan printout, confirmation before paid calls, generation flags (sent only when given), and a run manifest.
- `total_cost_usd` is `null` when any trial's cost is unknown; real providers never hard-code prices. Mock runs still report 0.0.
- Tests: 37 adapter tests (fake SDK client) plus 9 wire-level tests (real SDK, in-memory HTTP transport). No network and no paid calls.
- Mutations M24 (SDK retries re-enabled) and M25 (redaction disabled) added.
- **Unchanged:** tasks, policy text, gold answers, oracle, grader, weights, evidence rule, pairs, taxonomy, metric definitions.
- **Not yet done:** a real paid call. This cloud session has no `OPENAI_API_KEY`, and its network policy blocks `api.openai.com`.

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

## v0.5: long-timeline candidates (tasks/pilot3) and text-only prompt v3
- Real runs (prompt v2, 3 trials): pilot2 = 100% for Opus, Sonnet, Haiku; dev = 100% / 100% / 92%.
- tasks/pilot3: 4 pairs, 72-84 events each (noise, look-alike change IDs and sink IPs, late and relative
  extensions, local-time windows). Generators: scripts/gen_pilot2.py, scripts/gen_pilot3.py.
- Prompt v3 = v2 wording, timeline shown without the structured `details` (they pre-compute answers).
- Probe (1 trial each): pilot3 v2: Opus 8/8, Haiku 8/8. pilot3 v3: Opus 8/8, Haiku 7/8 (miss was a task
  bug, REVIEW-029).
- Finding so far: current Claude models do not fail this task family at the difficulty built so far.
