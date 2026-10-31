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
| `scopeshift/providers/` | provider interface, mock providers, OpenAI provider (Responses API) |
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
python scripts/mutation_check.py        # injects 25 bugs; all must be caught
python run_eval.py --provider mock --model mock-perfect --tasks tasks/development --trials 3
```

Mock models: `mock-perfect`, `mock-wrong-action`, `mock-missed-transition`,
`mock-premature-transition`, `mock-source-authority-confusion`, `mock-malformed`, `mock-empty`,
`mock-timeout`, `mock-flaky`. No API key is needed.

Per-trial records go to `transcripts/<run_id>.jsonl`. Run summaries and metrics go to
`results/<run_id>.json`. Both are git-ignored.

## Running a real model (OpenAI)

> **Real API calls can incur charges on your account.** Every paid run first prints the plan
> (provider, model, number of tasks, trials and total calls) and asks for confirmation. `--yes`
> confirms non-interactively.

1. **Install.** `pip install -r requirements.txt` (includes the official `openai` SDK, pinned).
2. **Set the key in your shell.** Never put it in a file you commit:

   ```bash
   export OPENAI_API_KEY="<your key>"
   ```

   `.env` and `.env.*` are git-ignored. `.env.example` lists the variable name only. The key is
   read from the environment, never printed (the plan shows only `set` / `NOT SET`), and is
   redacted from any error text stored in results.
3. **Dry run.** Validates tasks and settings and prints the plan. Makes zero model calls:

   ```bash
   python run_eval.py --provider openai --model <MODEL> --trials 5 --dry-run
   ```

   Optional: `--validate-api` checks that the key works and the model is accessible by
   retrieving the model's metadata. No output is generated.
4. **One real request** (1 task × 1 trial = 1 call):

   ```bash
   python run_eval.py --provider openai --model <MODEL> --task-ids SS-DEV-001A --trials 1
   ```

5. **All 4 tasks once** (4 calls):

   ```bash
   python run_eval.py --provider openai --model <MODEL> --trials 1
   ```

6. **Pilot: 4 tasks × 5 trials** (20 calls):

   ```bash
   python run_eval.py --provider openai --model <MODEL> --trials 5
   ```

7. **Results:**
   - `results/<run_id>.json`: run manifest and metrics. It records the requested and returned
     model IDs, generation settings, start/end time, provider-reported token usage, Python,
     package and git versions, and sha256 hashes of the prompt template and task files.
   - `transcripts/<run_id>.jsonl`: one line per trial. Each line has the raw model text
     (`raw_response`, exactly as returned), the parsed answer, parse error, component scores,
     strict pass, failure types, latency, attempts and retries, tokens, provider response ID,
     returned model, response status, a JSON dump of the provider response, and the
     infrastructure error kind if any.
   - `transcripts/<run_id>.prompts.json`: the exact system and user prompt sent for each task.
     Every trial records their sha256 hashes.

**What the model receives:** the system prompt as `instructions` and the incident JSON as
`input`. With the default `--structured-output json_schema`, it also receives a strict JSON
schema restating the answer contract the prompt already gives: the six fields, their types and
the two enums. Length, item-count and ID-format limits are *not* in the schema, so breaking
them still shows up as a parse failure. `--structured-output off` sends the prompt alone.
`store=false` is set. The model never receives task IDs, pair or variant information,
reference answers or oracle data. No second model judges, cleans up or re-asks: the
deterministic grader scores the raw text.

**Generation settings:** `--temperature`, `--max-output-tokens` and `--reasoning-effort` are
sent **only when given**, because some models reject them. Every run records exactly what was
sent. Even with `--temperature 0`, OpenAI does not guarantee identical outputs, so the eval runs
several trials and reports pass^k.

**Infrastructure failure vs model failure:**
- **Infrastructure failure** (`infrastructure_failure: true`, never scored against the model):
  timeouts, network errors, 429 rate limits, 5xx errors and responses the API marks as failed
  are retried up to `--max-attempts` (default 3) with exponential backoff (2 s base) and ±25%
  jitter. Auth errors, quota exhaustion and bad requests (e.g. an unknown model or unsupported
  parameter) stop immediately. The SDK's own retries are disabled, so every attempt is counted.
- **Model failure:** any completed response. That includes wrong answers, malformed or
  truncated JSON, empty output and refusals. These are graded as returned and are **never
  retried**.

**Cost:** token usage reported by the API is recorded per trial and summed per run. Dollar cost
is *not* computed, because no pricing is hard-coded. Multiply `usage_reported` by the model's
current price. Usage of failed attempts is unknown and is recorded as `null`, never guessed.

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
