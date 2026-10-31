# BUG_LOG

Bugs found during the pilot bug hunt (10). Every entry has a regression test; all are fixed.

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
- **Fix:** The tag additionally requires the answer to cite a rejected non-authoritative event (e.g. E4) as evidence. Otherwise the result is tagged only `MISSED_TERMINATION` / `STALE_AUTHORIZATION`.
- **Regression test:** `tests/test_regressions.py::test_bug010_authority_confusion_requires_citing_rejected_event`, `tests/test_mock_provider.py::test_mock_missed_transition`
