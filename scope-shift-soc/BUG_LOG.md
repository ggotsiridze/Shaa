# BUG_LOG

BUG-001 to BUG-010: found during the original pilot bug hunt. REVIEW-011 to REVIEW-020: found by an external adversarial review. REVIEW-024: found by a second external review.
REVIEW-021 to REVIEW-023, REVIEW-025 and REVIEW-026: found while applying repairs or during the author's own re-review. Every entry has a regression test, and all are fixed.

## BUG-001: missed-transition mock failed for the wrong reason
- **File:** `scopeshift/providers/mock.py`
- **Symptom:** `mock-missed-transition` failed SS-DEV-001A, a variant where "always ACTIVE" is the correct answer.
- **Root cause:** The mock cited `[first event, alerted event]` as evidence. That leaves out the authoritative extension E4, which 001A requires.
- **Impact:** The mock's failure profile would have suggested evidence was the problem when the intended failure was state tracking. Metrics for that mock were misleading.
- **Fix:** The oracle now exposes `authorizing_event_ids`. The mock cites those plus the alerted event.
- **Regression test:** `tests/test_regressions.py::test_bug001_missed_transition_mock_cites_authorizing_events`, `tests/test_mock_provider.py::test_mock_missed_transition`

## BUG-002: CRLF-fenced response rejected
- **File:** `scopeshift/grader.py`
- **Symptom:** A whole-response ```` ```json ```` fence with `\r\n` line endings was reported as "invalid JSON".
- **Root cause:** The fence regex only accepted `\n`.
- **Impact:** A valid answer from a provider or OS that emits CRLF would be scored as a PARSE_FAILURE.
- **Fix:** The regex accepts `\r?\n` at both fence boundaries. Nothing may still appear before or after the fence.
- **Regression test:** `tests/test_regressions.py::test_bug002_crlf_fence_parses`

## BUG-003: inverted authorization window silently accepted
- **File:** `scopeshift/oracle.py`
- **Symptom:** A CREATE record with `valid_until <= valid_from` produced `NOT_APPLICABLE` with no error.
- **Root cause:** No sanity check on the window.
- **Impact:** A typo in a task file would silently produce a plausible-looking gold label.
- **Fix:** The oracle raises `ValueError`, which `check_against_reference` turns into a task-validation failure.
- **Regression test:** `tests/test_regressions.py::test_bug003_inverted_window_rejected`

## BUG-004: missing structured field crashed validation
- **File:** `scopeshift/oracle.py`
- **Symptom:** A task missing `details.valid_until` made `validate_tasks.py` and `run_eval.py` crash with a bare `KeyError` that didn't name the task.
- **Root cause:** Direct dict indexing, with no error handling in `check_against_reference`.
- **Impact:** A confusing traceback instead of a task-level validation failure.
- **Fix:** A `_detail()` helper raises a descriptive `ValueError`. `check_against_reference` reports oracle errors as a validation problem that names the task.
- **Regression test:** `tests/test_regressions.py::test_bug004_missing_details_reported_not_crash`

## BUG-005: mock answer-key collision was silent
- **File:** `scopeshift/providers/mock.py`
- **Symptom:** Two tasks that render identical prompts were accepted. The later one silently overwrote the earlier one in the prompt-hash answer key.
- **Root cause:** A dict comprehension with no collision check.
- **Impact:** A mock could answer task X with task Y's reference, so harness results would be wrong.
- **Fix:** `MockProvider.from_tasks` raises on identical prompts. (The pair validator already guarantees the variants differ.)
- **Regression test:** `tests/test_regressions.py::test_bug005_identical_prompts_rejected_by_mock`

## BUG-006: misnamed task files silently ignored
- **File:** `scopeshift/loader.py`
- **Symptom:** `ss-dev-003a.json` (lowercase) in the task directory was skipped without warning.
- **Root cause:** The loader globbed only `SS-*.json`.
- **Impact:** A task could silently drop out of a run, and pass rates would be computed over the wrong set.
- **Fix:** Any `*.json` other than `pairs.json` and `SS-*.json` is now an error.
- **Regression test:** `tests/test_regressions.py::test_bug006_stray_task_file_rejected`

## BUG-007: `--trials 0` produced a traceback
- **File:** `run_eval.py`
- **Symptom:** An uncaught `ValueError` traceback.
- **Root cause:** No CLI-level argument check.
- **Impact:** Cosmetic and robustness only. No incorrect results.
- **Fix:** The CLI returns exit code 2 with a message for `--trials < 1` or `--max-attempts < 1`.
- **Regression test:** `tests/test_regressions.py::test_bug007_zero_trials_clean_error`

## BUG-008: mutation checker ran stale bytecode
- **File:** `scripts/mutation_check.py`
- **Symptom:** After all mutations were reverted and the source was byte-identical, the test suite still failed 3 extra-field tests.
- **Root cause:** Mutation M7 (`"forbid"` → `"ignore"`) has the same length as the original and was written and reverted within one second. The source mtime and size were unchanged, so CPython reused the mutated `.pyc` in `__pycache__`.
- **Impact:** Mutation results could be wrong in both directions (false kills and false survivors), and the working tree was left behaving incorrectly.
- **Fix:** Clear `__pycache__` before every test run and run with `PYTHONDONTWRITEBYTECODE=1`.
- **Regression test:** `scripts/mutation_check.py` itself re-runs the full suite after reverting and fails if it doesn't pass ("after revert: 173 passed").

## BUG-009: test gap in oracle-vs-reference cross-check
- **File:** `tests/` (test coverage gap, found by mutation M12)
- **Symptom:** Mutation M12 made `check_against_reference` copy status and transition from `hidden_reference` (so it stopped checking them), and every test still passed.
- **Root cause:** The only tampered-reference test also changed rule, action and evidence, so it caught the mismatch through those other fields.
- **Impact:** A regression that made task validation trust hidden labels for state and transition would have gone unnoticed.
- **Fix:** Added tests where the reference differs from the oracle *only* in status and transition, and *only* in evidence.
- **Regression test:** `tests/test_regressions.py::test_bug009_reference_status_and_transition_checked_independently`, `::test_bug009_reference_evidence_checked`

## Checked: no defect found

| Area | How it was checked |
|---|---|
| Off-by-one at expiry | Boundary tests at −1 s, exactly at expiry, +1 s, and at a non-UTC offset (`test_time_boundaries.py`); mutation M2 |
| Time zones | `Z` vs `+00:00` vs `+01:00` compared as instants; naive timestamps rejected in task fields and in `details` |
| Retroactive revocation | `event_states` shows earlier events stay ACTIVE (`test_no_retroactive_revocation`) |
| First vs later transition | `test_first_transition_recorded`; mutation M6 |
| Source authority | Both `source` *and* `source_class` must match; 5 near-miss combinations rejected; mutation M1 |
| Hidden answer leakage | 21 forbidden tokens; the prompt's JSON is exactly `model_visible`; task IDs absent (they end in the variant letter); mutation M5 |
| Shared state between trials | A recording provider shows a fresh prompt object per trial, with no prior answer text; task objects are frozen and unchanged after a run |
| Duplicate trials / task IDs | `(task_id, trial_index)` is unique; duplicate task IDs are rejected by the loader and by the harness |
| Retry bugs | Retries are bounded; completed responses (even malformed) are never retried; AUTH errors are not retried; backoff is exponential; mutation M11 |
| API errors scored as model failures | Infra trials have `score=None`, `model_failure=False` and are excluded from model-quality denominators (rates are `None`, not `0`, when nothing was scored); mutation M8 |
| Cache contamination | No response cache exists. The mock keys only on the prompt hash, and collisions are rejected (BUG-005) |
| Silent exceptions | No bare `except`. Unexpected provider exceptions (not `InfrastructureError`) propagate and crash the run rather than being mislabeled |
| Cost accounting | Cost and tokens come from the completed response only. See the remaining concern in DEVELOPMENT_LOG.md |

## BUG-010: SOURCE_AUTHORITY_CONFUSION over-attributed
- **File:** `scopeshift/taxonomy.py`
- **Symptom:** In the required `mock-missed-transition` run, SS-DEV-001B was tagged `SOURCE_AUTHORITY_CONFUSION`, although that mock never looks at the ticket comment.
- **Root cause:** The rule tagged any answer whose state matched the "accept every source" oracle. On 001B, "still ACTIVE" is also exactly what a model that ignores all transitions produces.
- **Impact:** Inflated the source-authority failure count. That violates the requirement to classify only when the category can be reliably determined.
- **Fix:** The tag additionally required the answer to cite a rejected non-authoritative event (e.g. E4) as evidence. **Superseded by REVIEW-021**: once the prompt asks every model to cite rejected claims, citation no longer signals trust.
- **Regression test:** `tests/test_regressions.py::test_bug010_authority_confusion_requires_citing_rejected_event`, `tests/test_mock_provider.py::test_mock_missed_transition`

---

# External review findings (v0.2 repair)

## REVIEW-011 (BLOCKER): Pair 1 required a "signed" extension without exposing signature evidence
- **File:** `tasks/development/SS-DEV-001A.json`, `SS-DEV-001B.json` (policy CHG-01), event texts
- **Symptom:** CHG-01 allowed only "signed records" to change authorization, but no event shows a signature or its validation.
- **Impact:** A careful analyst could reasonably reject the authoritative extension in 001A, so 001A's gold answer was not the only defensible answer.
- **Fix:** Removed "signed" everywhere. CHG-01 now says "Only records from the Change Management System (source: change_management) may ...". No cryptographic modelling.
- **Regression test:** `tests/test_review_repairs.py::test_change_record_no_signature_requirement`

## REVIEW-012 (BLOCKER): Pentest scope wording conflicted with in-scope lateral movement
- **File:** `SS-DEV-002A/B.json` (PT-02, E1 record, structured details), `scopeshift/oracle.py`
- **Symptom:** PT-02 covered only activity "from the approved source". E3 (PsExec `stg-web-01 → stg-app-02`) and E7 (LSASS on `stg-app-02`) do not originate from the tester IP.
- **Impact:** It was defensible to say coverage ended before E6, so E6 was not a unique transition.
- **Fix:** PT-01/PT-02 now distinguish the **approved initial ingress source** from **activity on approved targets** and **approved lateral-movement methods between approved targets**. The RoE record lists PsExec as the only lateral method and LSASS dumping as an approved on-target method. PT-02 states that covered activity "does not by itself end the authorization". The oracle checks each of these from visible details.
- **Regression test:** `tests/test_review_repairs.py::test_in_scope_lateral_movement_is_covered`, `::test_psexec_between_approved_targets_is_covered`, `::test_lsass_on_approved_target_with_approved_method_is_covered`, `::test_approved_sink_transfer_does_not_terminate`, `::test_unapproved_external_sink_is_first_termination`

## REVIEW-013 (MAJOR): Time-based transition used the first post-expiry activity, not the expiry boundary
- **File:** Pair 1 tasks, `scopeshift/oracle.py` (`check_against_reference`)
- **Symptom:** The window ended at 04:00, but the reference transition was the 04:15 activity, because no event existed at 04:00.
- **Impact:** The correct reasoning "it expired at 04:00; E6 is just the first later activity" would have been marked wrong.
- **Fix:** Added an identical neutral `system_clock` / `SYSTEM_EVENT` time marker at exactly 04:00:00Z to both variants (new E6). The activity moved to E7. Task validation now fails if an expiry transition is not exactly at the window end. `SYSTEM_EVENT` can never hold authority (schema).
- **Regression test:** `::test_expiry_occurs_at_clock_boundary_event`, `::test_post_expiry_activity_not_transition_event`, `::test_task_validation_requires_boundary_marker_for_expiry`, `::test_boundary_marker_identical_in_both_variants`, `::test_system_event_cannot_hold_authority`, `tests/test_oracle.py::test_time_marker_cannot_change_state`

## REVIEW-014 (MAJOR): Source-class names leaked the answer
- **File:** `scopeshift/schema.py` (`SourceClass`), all tasks
- **Symptom:** Pair 1 differed by `AUTHORITATIVE_POLICY_SOURCE` vs `INTERNAL_INFORMATIONAL_SOURCE`.
- **Impact:** A model could pass by trusting the label, without applying the policy.
- **Fix:** Replaced the enum with descriptive record types: CHANGE_RECORD, ENGAGEMENT_RECORD, TICKET_RECORD, CHAT_MESSAGE, EMAIL_MESSAGE, ENDPOINT/NETWORK/DLP_TELEMETRY, SYSTEM_EVENT. Pair 1 now differs by `change_management`/`CHANGE_RECORD` vs `ticketing`/`TICKET_RECORD`. Authority comes only from the policy text and the hidden `oracle_spec`.
- **Regression test:** `::test_source_class_name_does_not_encode_authority_semantics`, `tests/test_pair_integrity.py::test_pair_diff_matches_expected`

## REVIEW-015 (MAJOR): Oracle independence was overstated
- **File:** `README.md`, `RESEARCH_DECISION.md`, `DEVELOPMENT_LOG.md`, `scopeshift/oracle.py` docstring
- **Symptom:** The docs said the gold answer "does not come from hand-written labels" and that the oracle "independently derives gold".
- **Impact:** This overclaimed. The oracle relies on a hand-authored `oracle_spec`.
- **Fix:** Every location now uses the precise wording: it validates the reference against a deterministic state machine backed by a hand-authored encoding of the visible policy. This catches label/spec inconsistencies but does not prove the encoding matches the English policy, which requires human review.
- **Regression test:** documentation-only. `tests/test_review_repairs.py` and the mutation check M12 verify what the oracle *does* check.

## REVIEW-016 (MAJOR): `source_authority_accuracy` was only a state/transition proxy
- **File:** `scopeshift/metrics.py`, `run_eval.py`
- **Symptom:** Per-task status and transition correctness on probe tasks was reported as "source authority accuracy".
- **Impact:** A model that ignored the extension entirely got authority credit on 001B.
- **Fix:** Removed the metric. Added `source_authority_pair_pass_rate` (both variants strictly pass at the same replicate index) and `authority_contrast_consistency` (both variants have the correct status and transition at the same replicate index). Documented as behavioural sensitivity to provenance, not internal reasoning.
- **Regression test:** `::test_authority_pair_metric_requires_both_variants_correct`, `::test_authority_pair_metric_fails_if_only_A_correct`, `::test_authority_pair_metric_fails_if_only_B_correct`, `::test_authority_metric_ignores_non_authority_pairs`; mutation M17

## REVIEW-017 (MAJOR): Automatic hindsight label inferred internal reasoning
- **File:** `scopeshift/taxonomy.py`
- **Symptom:** `HINDSIGHT_RECLASSIFICATION` was assigned automatically from the chosen event alone.
- **Impact:** It claimed a reasoning process that the output cannot show.
- **Fix:** Renamed the automatic category to the observational `EARLY_TRANSITION_ON_ACTIVE_EVENT`. `HINDSIGHT_RECLASSIFICATION` is now a manual-only label (`MANUAL_ONLY_LABELS`), for human transcript review when the rationale supports it.
- **Regression test:** `tests/test_wrong_answers.py::test_early_transition_on_active_event`, `::test_no_automatic_hindsight_claim`, `::test_later_wrong_transition_is_not_early`

## REVIEW-018 (MINOR): Required evidence omitted the decisive contrast event
- **File:** tasks, `scopeshift/oracle.py`, `prompts/model_under_test_v1.md`
- **Symptom:** 001B could strictly pass without citing the rejected extension E4, and 002A without citing E6.
- **Impact:** Strict pass did not show the model had looked at the event that decides the pair.
- **Fix:** The prompt now states rule (a)/(b)/(c): authorization record(s) and permitted changes; claims that did not take effect; then the transition event if TERMINATED, otherwise the alerted event plus the latest event evaluated against the stop condition. The oracle derives exactly this. New sets: 001A {E1,E4,E7}, 001B {E1,E4,E6}, 002A {E1,E6,E7}, 002B {E1,E6}. Each pair's decisive event is required in both variants.
- **Regression test:** `::test_pair1B_requires_rejected_extension_evidence`, `::test_pair2A_requires_approved_sink_event`, `::test_pair_mates_cite_same_decisive_event`; mutation M18

## REVIEW-019 (MINOR): All-trials metric was called pass@k
- **File:** `scopeshift/metrics.py`, `run_eval.py`, README
- **Symptom:** `strict_pass_at_3` meant "all 3 trials pass", which is pass^k, not pass@k.
- **Fix:** Renamed to `strict_pass_pow_k` / `pair_pass_pow_k` (shown as "pass^k (all k trials pass)"). Added a true pass@k, `strict_pass_at_k_any`, labelled "reference only".
- **Regression test:** `::test_pass_pow_k_vs_pass_at_k`, `tests/test_mock_provider.py::test_mock_perfect` (old key absent)

## REVIEW-020 (MINOR): Environment not pinned
- **File:** `requirements.txt`, `pyproject.toml`, `.python-version`
- **Fix:** Moved to Python 3.12.3 (installed alongside 3.13). Exact pins for all direct and transitive packages, taken from the working environment (pydantic 2.14.2, pytest 9.2.0, ruff 0.16.9, ...). `pip check` is clean.
- **Regression test:** `pip check` and the full suite run in the pinned 3.12 venv.

## REVIEW-021 (found during repair): BUG-010's citation gate was invalidated by REVIEW-018
- **File:** `scopeshift/taxonomy.py`
- **Symptom:** After REVIEW-018 the prompt asks every model to cite rejected claims, so the missed-transition mock (which cites E4 as rejected) was tagged SOURCE_AUTHORITY_CONFUSION.
- **Root cause:** On 001B, "trusted the ticket comment" and "missed the expiry" both produce (ACTIVE, null). The citation was the only signal, and it no longer carries meaning.
- **Impact:** The authority failure count would have been inflated.
- **Fix:** Assign SOURCE_AUTHORITY_CONFUSION only when the authority-blind answer differs from both the truth and the "nothing ended" answer (ACTIVE, null). It is never auto-assigned on 001B. Authority there is measured by the pair metrics (REVIEW-016).
- **Regression test:** `tests/test_regressions.py::test_bug010_review021_authority_confusion_not_overattributed`, `tests/test_wrong_answers.py::test_source_authority_confusion_tagged_when_distinguishable`, `::test_pair1b_authority_confusion_not_auto_tagged`; mutation M19

## REVIEW-022 (found during repair): PT-05 and PT-03 both applied after the stop condition
- **File:** Pair 2 policy text
- **Symptom:** Once activity after the stop condition counted as "not covered", it matched both PT-03 (OPEN_INCIDENT) and PT-05 (uncovered activity → ESCALATE_TIER2).
- **Impact:** 002B had two defensible governing rules and actions.
- **Fix:** PT-05 now begins its escalation clause with "Except where PT-03 applies".
- **Regression test:** `tests/test_review_repairs.py::test_pt05_defers_to_pt03_in_policy_text`

## REVIEW-023 (found during repair): Authority encoding stricter than the policy text
- **File:** Pair 1 and Pair 2 policy text (CHG-01, PT-01, PT-04)
- **Symptom:** The oracle grants authority only when `source` **and** `source_class` match, but the visible policy named only the source.
- **Impact:** No effect on the 4 pilot tasks, where the two always agree. But it is exactly the spec/text gap REVIEW-015 warns about: a future task with a matching source and a different record type would have a gold answer the visible text does not support.
- **Fix:** The policy text now names both fields, e.g. "(source: change_management, source_class: CHANGE_RECORD)". Identical in both pair variants.
- **Regression test:** `tests/test_review_repairs.py::test_policy_text_names_every_authority_grant`

---

# Second external review (v0.3 repair)

## REVIEW-024 (BLOCKER, second external review): Pair 2 transfer coverage did not prove the approved method
- **File:** `SS-DEV-002A/B.json` (E4, E6 details), `scopeshift/oracle.py` (`_Scope.covers`)
- **Verified before fixing:** confirmed. E4/E6 details contained only `src_host`, `destination_ip`, `destination_host` and `data_classification`. `covers()` returned `EXFIL_TECHNIQUE in self.methods and ...`, which checks that exfiltration simulation is in E1's approved list, not that this transfer used it.
- **Root cause:** PT-02 condition (2), "it uses an approved method", was inferred from the authorization rather than read from the event.
- **Impact:** For 002A it was defensible to argue that E6 was not shown to be covered under PT-02, so PT-05 / ESCALATE_TIER2 applied. Gold uniqueness was broken. (See REVIEW-025: the oracle's own answer would not even have changed, which is a second gap.)
- **Fix:**
  - E4 and E6 now carry `"technique": "exfiltration_simulation"`, identical in A and B, consistent with the `technique` field on E2, E3 and E7.
  - `covers()` now applies the method check (the event's own `technique` must be in the approved list) to transfers too, before the transfer-specific checks. A missing technique means not covered.
  - Transfer-specific checks: the source host must be an approved target. The destination must be the sink when the data is PROTECTED (PT-02 (4)) or the technique is `exfiltration_simulation` (E1: "to the approved sink only").
  - This also corrects a smaller mismatch: v0.2 required *every* transfer to go to the sink, but the English text restricts only protected data and the exfiltration-simulation method.
  - The PT-03 stop condition is unchanged and independent of technique, as in the policy text.
- **Regression tests:** `tests/test_review_repairs.py::test_transfer_with_exfil_technique_scope_and_sink_is_covered`, `::test_transfer_without_technique_is_not_covered`, `::test_transfer_with_unapproved_method_is_not_covered`, `::test_transfer_from_out_of_scope_host_is_not_covered`, `::test_approved_technique_unapproved_sink_is_stop_condition`, `::test_stop_condition_does_not_depend_on_technique`, `::test_stop_condition_needs_permitted_telemetry_even_with_technique`, `::test_approved_technique_and_sink_does_not_trigger_stop`, `::test_exfil_simulation_of_unprotected_data_off_sink_not_covered_but_no_stop`, `::test_pilot_transfers_carry_explicit_approved_technique`, `::test_e6_technique_identical_across_pair`, `::test_pair2_gold_after_technique_repair`, `::test_pair_diff_still_only_declared_fields`
- **Mutations:** M20 (skip the transfer technique check), M21 (drop the sink restriction). Both killed.

## REVIEW-025 (found while verifying REVIEW-024): Uncovered earlier activity was invisible to the gold rule
- **File:** `scopeshift/oracle.py` (`check_against_reference`)
- **Symptom:** With E6's technique removed from 002A, the oracle still derived PT-02 / MONITOR, because the governing rule is chosen from the *alerted* event (E7) only. PT-05, however, requires escalation of *any* uncovered engagement activity (CHG-04 likewise).
- **Impact:** A task with uncovered pre-transition activity has two simultaneous obligations, so the gold action is not unique. The validator did not detect this. The four pilot tasks were not affected after REVIEW-024, but the gap would have let such a task through silently.
- **Fix:** Task validation now fails when any ACTIVITY/DATA_TRANSFER event before the transition (or anywhere, if there is no transition) is uncovered. Uncovered events after a transition are expected (the terminating rule governs them).
- **Regression tests:** `::test_validation_rejects_uncovered_activity_before_transition`, `::test_uncovered_activity_after_transition_is_expected`, `::test_pilot_tasks_have_no_competing_obligations`
- **Mutation:** M22. Killed.

## REVIEW-026 (found in the author's re-review): CHG-02 "matches the approved activity description" was not checked
- **File:** `SS-DEV-001A/B.json` (E1 details), `scopeshift/oracle.py`
- **Symptom:** CHG-02 covers activity by the approved account, on the approved host, *that matches the approved activity description*. The oracle checked only host and account. It is the same class of gap as REVIEW-024, in Pair 1.
- **Impact:** None on the current gold answers (E2, E3 and E7 are PowerShell patch scripts and the named installer). But a visible requirement was silently ignored by the encoding.
- **Fix:** E1 now carries `approved_processes: "powershell.exe,dbengine_update.exe"`, matching its text and identical in both variants. Maintenance coverage requires the event's own `process` to be in that list.
- **Regression tests:** `::test_maintenance_unapproved_process_not_covered`, `::test_maintenance_missing_process_not_covered`, `::test_pair1_activities_match_approved_processes`
- **Mutation:** M23. Killed.

---

# Real-model pilot findings (v0.4)

## REVIEW-027 (found in the real pilot): rationale length limit caused non-capability failures
- **File:** `scopeshift/schema.py` (`MAX_RATIONALE_CHARS`), prompt
- **Symptom:** claude-haiku-5-5 failed 3 of 20 trials as PARSE_FAILURE. Every scored field was correct; the only problem was a rationale of about 790 characters against the 600 limit.
- **Impact:** A not-scored formatting rule lowered Haiku's pass^5 from 1.0 to 0.5. The benchmark would have reported a "limitation" that was really verbosity.
- **Fix:** The limit was raised to 2000 in schema, grader requirements, prompt v2 and the alignment doc. The saved raw answers were re-graded offline (`scripts/regrade.py`, no API calls), and the original summaries are kept. The re-graded file notes that the model had been instructed with 600.
- **Regression tests:** `tests/test_parser.py::test_very_long_rationale` (now uses the constant); alignment check on `at most 2000 characters`.

## REVIEW-028 (found by the pair validator while building pilot2): alert source differed within a pair
- **File:** `tasks/pilot2/SS-DEV-005A/B.json`
- **Symptom:** The alert `source` was copied from the last event (`edr` vs `dlp`). That made an undeclared model-visible difference, which hints at the answer.
- **Fix:** The alert source is `siem` in both variants.
- **Regression tests:** `tests/test_pilot2_tasks.py::test_pairs_differ_only_in_declared_fields`, `::test_stop_condition_needs_permitted_telemetry` (alert identical across the pair).

## REVIEW-029: SS-DEV-007A alerted event named an unapproved binary (task bug, found by a real run)
- Found: Haiku (prompt v3, 1 trial) answered ACTIVE / CHG-05 / ESCALATE_TIER2 on 007A. Its rationale said the
  alerted PowerShell script "started disp+work.exe", which is not an approved process.
- Cause: the event text said the script started disp+work.exe; the oracle only saw process=powershell.exe.
  The model's reading was defensible, so the gold answer was ambiguous.
- Fix: the alerted event now runs post_patch_check.ps1 only. Tasks regenerated; validators pass.
  The run that found it is kept in results/ unchanged (it was graded against the old task).
