# ScopeShift-SOC

**Temporal Exception-State Tracking for AI SOC Analysts**: development pilot.

> Status: **pilot software only.** 4 synthetic development tasks (2 controlled pairs). No real-model
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
| `scopeshift/oracle.py` | deterministic INACTIVE→ACTIVE→TERMINATED state machine; independently derives gold |
| `scopeshift/grader.py` | fail-closed parser + weighted deterministic grader + prompt-requirement registry |
| `scopeshift/taxonomy.py` | failure classification (only when reliably determinable) |
| `scopeshift/pair_validation.py` | flattened model-visible diff; must equal declared differences |
| `scopeshift/alignment.py` | prompt/grader alignment check |
| `scopeshift/harness.py`, `metrics.py` | isolated trials, bounded retries, pilot metrics, pass@k |
| `scopeshift/providers/` | provider interface + mock providers (no real providers yet) |
| `scripts/` | `validate_tasks.py`, `validate_pairs.py`, `validate_alignment.py`, `mutation_check.py` |

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q
python scripts/validate_tasks.py
python scripts/validate_pairs.py        # also regenerates PAIR_DIFF_REPORT.md
python scripts/validate_alignment.py
python scripts/mutation_check.py        # injects 12 bugs; all must be caught
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
- **Time-based transition event:** the first timeline event at or after the end time.
- **No retroactive changes:** revocation and stop conditions take effect at their own event.
- **Authority:** a state change counts only if the event's `source` **and** `source_class` match
  a grant in the policy.
- **Strict pass:** status, transition, rule and action are all correct, every required evidence
  ID is cited, and no unknown IDs appear.
- **Infrastructure failures** (timeout, 429, 5xx, network, transport, auth) are never model
  failures. They are excluded from model-quality denominators.

All data is synthetic. Domains use `example`/`example.net`. Public IPs come from the RFC 5737
documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24).
