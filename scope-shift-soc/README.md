# ScopeShift-SOC

**Temporal Exception-State Tracking for AI SOC Analysts**: development pilot.

> Status: **pilot software only, v0.3 (repaired after two external reviews)**. 4 synthetic development tasks (2 controlled pairs). No real-model
> results exist. No human or expert validation has happened (`human_review_status: PENDING`).
> AI coding tools assisted with implementation and drafting. Final author review pending.

## Question

Can an LLM acting as a SOC analyst track the lifecycle of an organization-specific operational
exception (maintenance window, pentest engagement) as telemetry arrives? Specifically, can it:

- identify the exact event where the exception stops governing,
- tell which sources have authority to change that state, and
- switch to the incident-response rule and action that then applies?

See `RESEARCH_DECISION.md` for scope, related work, non-claims, and pivot/abandon criteria.

## Layout

| Path | Purpose |
|---|---|
| `tasks/development/` | 4 pilot tasks + `pairs.json` (declared pair differences) |
| `prompts/model_under_test_v1.md` | system prompt + user template (model sees only `model_visible`) |
| `scopeshift/schema.py` | Pydantic models for tasks and the strict model-answer contract (`extra="forbid"`) |
| `scopeshift/oracle.py` | deterministic INACTIVE→ACTIVE→TERMINATED state machine over a hand-authored policy encoding; validates reference answers (see "What the oracle proves") |
| `scopeshift/grader.py` | fail-closed parser + weighted deterministic grader + prompt-requirement registry |
| `scopeshift/taxonomy.py` | failure classification (only when reliably determinable) |
| `scopeshift/pair_validation.py` | flattened model-visible diff; must equal declared differences |
| `scopeshift/alignment.py` | prompt/grader alignment check |
| `scopeshift/harness.py`, `metrics.py` | isolated trials, bounded retries, pilot metrics, pass^k |
| `scopeshift/providers/` | provider interface + mock providers (no real providers yet) |
| `scripts/` | `validate_tasks.py`, `validate_pairs.py`, `validate_alignment.py`, `mutation_check.py` |

## What the oracle proves

Reference answers are validated against a deterministic state machine backed by a hand-authored
machine-readable encoding of the visible policy (`oracle_spec`). This catches inconsistencies
between the stored reference answer and the encoded policy logic. It does not prove that the
machine-readable policy specification faithfully represents the natural-language policy. That
equivalence requires human review. Within that limit, the oracle evaluates visible values only:
timestamps, source, source_class and structured details (hosts, accounts, techniques,
destinations). It never reads the stored answer.

## Run

Tested with **Python 3.12.3**. All packages are pinned exactly in `requirements.txt`.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip check
pytest -q
python scripts/validate_tasks.py
python scripts/validate_pairs.py        # also regenerates PAIR_DIFF_REPORT.md
python scripts/validate_alignment.py
python scripts/mutation_check.py        # injects 23 bugs; all must be caught
python run_eval.py --provider mock --model mock-perfect --tasks tasks/development --trials 3
```

Mock models: `mock-perfect`, `mock-wrong-action`, `mock-missed-transition`,
`mock-premature-transition`, `mock-source-authority-confusion`, `mock-malformed`, `mock-empty`,
`mock-timeout`, `mock-flaky`. No API key is needed.

Per-trial records go to `transcripts/<run_id>.jsonl`. Run summaries and metrics go to
`results/<run_id>.json`. Both are git-ignored.

## Key conventions

- **Expiry boundary:** an exception is valid while `event_time < valid_until` and expired at
  `event_time >= valid_until`. Timestamps must be timezone-aware and are compared as instants.
- **Time-based transition event:** the first timeline event at or after the end time. Every task whose
  answer involves a time-based expiry places a neutral `SYSTEM_EVENT` time marker exactly at the window
  end, identical in both pair variants, so the transition is the real boundary. Task validation enforces this.
- **Source classes are descriptive** (`CHANGE_RECORD`, `TICKET_RECORD`, ...), never verdicts.
  Which sources may change state is stated only in the policy text.
- **No retroactive changes:** revocation and stop conditions take effect at their own event.
- **Authority:** a state change counts only if the event's `source` **and** `source_class` match
  a grant in the policy.
- **Strict pass:** status, transition, rule and action are all correct, every required evidence
  ID is cited, and no unknown IDs appear. Evidence is scored as required-evidence coverage
  (recall), not exact-set agreement: extra valid IDs within the cap of 4 are not penalized.
- **Coverage is read from each event's own visible details**, never inferred from the
  authorization alone. Maintenance: host, account and `process`. Pentest: `technique`, plus
  source/target/sink as applicable. Validation rejects any task in which activity before the
  transition is uncovered, because that would create a competing escalation obligation.
- **Headline consistency metric is pass^k:** a task passes only if *all* k trials pass
  (`strict_pass_pow_k`, `pair_pass_pow_k`). `strict_pass_at_k_any` (true pass@k: at least one
  trial passes) is reported for reference only.
- **Source authority is measured on the controlled pair:** `source_authority_pair_pass_rate`
  (both variants strictly pass in the same replicate) and `authority_contrast_consistency` (both
  variants have the correct status and transition in the same replicate). These measure
  behavioural sensitivity to provenance, not internal reasoning.
- **Failure categories are observational.** `HINDSIGHT_RECLASSIFICATION` is manual-only.
- **Infrastructure failures** (timeout, 429, 5xx, network, transport, auth) are never model
  failures. They are excluded from model-quality denominators.

All data is synthetic. Domains use `example`/`example.net`. Public IPs come from the RFC 5737
documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24).
