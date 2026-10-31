# RESEARCH_DECISION — ScopeShift-SOC (development pilot)

Status: **pilot design, revised after external review (v0.2), pre-model-testing**. No model results exist yet. No human or expert
validation has happened yet. Every task is synthetic.

## 1. Hypothesis

When an LLM acting as a Tier-1 SOC analyst reads an incident timeline that begins under a
valid, organization-specific operational exception (a maintenance window, a penetration
test), it will often fail to:

1. notice the exact event where that exception stops governing (expiry, revocation,
   explicit stop condition),
2. tell apart evidence that is *allowed* to change authorization state from evidence that only
   *sounds* authoritative, and
3. switch to the incident-response rule and action that apply once the exception has ended,

while still not over-reacting to frightening activity that remains covered.

This is a hypothesis. The pilot only checks whether the instrument is sound enough to test it.

## 2. Why this matters in a real SOC

- Suppression and allow-listing tied to change tickets and pentest engagements is everyday
  practice. Windows overrun, get extended informally ("I'll update the ticket"), or are revoked
  mid-flight.
- The classic failure is to anchor on the first context and keep suppressing ("this host is in
  maintenance") after the window has closed. Attackers can hide inside a known window, or right
  after it.
- The opposite failure also costs: escalating an authorized red-team action that merely looks
  hostile burns Tier-2 time and can disrupt an engagement.
- Authority matters. In most organizations a ticket comment or a chat message does not extend a
  change; only the change-management system of record does.

## 3. Prior benchmarks inspected (treated as related work; nothing copied)

| Work | What it already covers |
|---|---|
| shandure/soc-triage-benchmark | structured SOC schemas, verdict/severity/action, deterministic scoring, provider adapters, caching/retries, transcripts, false alarms, critical misses, change-ticket context |
| SolomonSmith-dev/soc-triage-ai | adversarial scenarios, approved maintenance, benign lookalikes, contradictory evidence, unverified pentests, impossible travel |
| Abhiro0p/SOCBench | broad SOC reasoning, evidence correlation, timelines, MITRE mapping, remediation, hallucination penalties |
| garrett-williams/ai-security-reasoning-benchmark | uncertainty, conflicting/missing evidence, approved admin activity, false positives |
| Dxsssu/SecAlertBench | attack vs non-attack classification, P/R/F1/FPR |
| Digital-Trust-Lab/AuthGateBench | hundreds of explicit contrast pairs |
| trace-ai-labs/PACT | binding vs non-binding cases, rule-scope discernment, repeated trials, pass^k consistency |
| PEACH-Research-Lab/AuthorizationBench | authorization boundaries, matched task pairs, harm/no-harm |
| JHU-CLSP/ManyIH | conflicting instructions, multiple privilege levels, hierarchy resolution |

Note: these descriptions come from the project brief. Whoever submits must check each repository
directly before claiming novelty against it.

## 4. Concepts already covered elsewhere (not claimed as ours)

Controlled contrast pairs; pass^k / repeated-trial consistency; approved-maintenance and
false-positive recognition; authorization vs harm; instruction or rule precedence; structured
SOC output schemas; deterministic graders; provider retry and caching infrastructure.

## 5. Claims we will NOT make

- That any model fails (or succeeds) at this. There is no data yet.
- That the tasks are expert-validated. `human_review_status` is `PENDING` everywhere.
- That contrast pairs, consistency metrics, authorization reasoning or rule precedence are novel.
- That 4 (or 8) tasks support statistical claims about model populations.
- That this is a general SOC benchmark.

## 6. Narrow ScopeShift-SOC contribution

**Temporal policy-state tracking under mixed-authority evidence.** One exception is followed
through a timeline. The scored target is:

- the *lifecycle state at the end of the timeline*,
- the *exact event at which it changed*,
- the *rule that governs after the change*, and
- the *action that rule requires*.

The visible policy states which sources may change authorization state. Source-class labels are
descriptive only (e.g. `CHANGE_RECORD`, `TICKET_RECORD`) and never verdict-like.

**What the oracle does and does not prove (revised after external review, REVIEW-015).**
Reference answers are validated against a deterministic state machine backed by a hand-authored
machine-readable encoding of the visible policy (`oracle_spec`). This catches inconsistencies
between the stored reference answer and the encoded policy logic, but it does not prove that
the machine-readable policy specification faithfully represents the natural-language policy.
That equivalence requires human review. Within that limit, the oracle evaluates visible values:
timestamps, `source`, `source_class` and structured details such as hosts, accounts, techniques
and destinations. It never reads `hidden_reference`.

## 7. Pilot Pair 1 — source authority / maintenance expiry (DEV-PAIR-001)

- E1 (03:00Z, `change_management` / `CHANGE_RECORD`) approves CHG-104 for
  `app-db-02.corp.example`, account `svc-patch`, window 03:00Z–04:00Z.
- E2/E3: PowerShell and installer activity by `svc-patch` on the approved host (covered).
- E4 (03:40Z): text "CHG-104 extended until 04:30 UTC", with a structured
  `new_valid_until = 04:30Z`.
  - **Variant A**: from `change_management` / `CHANGE_RECORD`.
  - **Variant B**: identical text, from `ticketing` / `TICKET_RECORD` (a ticket comment).
- E5 (03:52Z): progress comment in the ticket (both variants).
- **E6 (04:00:00Z, `system_clock` / `SYSTEM_EVENT`)**: a neutral time marker at the originally
  approved window end. It is identical in both variants and has no authority (enforced by the
  schema).
- E7 (04:15Z): the same PowerShell/installer pattern. The alert points at E7.
- Policy CHG-01 no longer mentions "signed" records (REVIEW-011). Only `change_management`
  records may change authorization state.

| Variant | Status | Transition | Rule | Action | Required evidence |
|---|---|---|---|---|---|
| A | ACTIVE | null | CHG-02 | DOCUMENT | {E1, E4, E7} |
| B | TERMINATED | E6 | CHG-04 | ESCALATE_TIER2 | {E1, E4, E6} |

- The pair differs only in `timeline[E4].source` and `timeline[E4].source_class`.
- Probes: STALE_AUTHORIZATION, plus source authority. Source authority is measured at pair
  level (§10), because on 001B the answer alone cannot separate "trusted the comment" from
  "missed the expiry".

## 8. Pilot Pair 2 — penetration-test stop condition (DEV-PAIR-002)

- E1 (`engagement_register` / `ENGAGEMENT_RECORD`) approves PT-2026-031. It defines:
  - the **approved initial ingress source** 198.51.100.23;
  - the **approved targets**: all hosts under `stg.corp.example`;
  - the **approved methods**: password spraying, PsExec lateral movement (the only method
    approved for lateral movement), LSASS credential dumping, and exfiltration simulation to
    the approved sink 203.0.113.50 only;
  - the window 09:00Z–17:00Z.

  The same values appear as structured details that the oracle checks.
- **PT-02 (revised, REVIEW-012)** covers three kinds of activity:
  - activity originating from the approved ingress source;
  - activity executed on an approved target;
  - an approved lateral-movement method executed from one approved target to another.

  In every case the activity must use an approved method, affect only approved targets, and
  send any protected data only to the sink. Covered activity "does not by itself end the
  authorization".
- **PT-05** escalates uncovered activity "except where PT-03 applies". That removes the overlap
  with the stop-condition rule.
- E2: password spraying from the ingress IP against `stg-dc-01`. E3: PsExec from `stg-web-01`
  to `stg-app-02`, both in scope. E4: protected test data sent to the approved sink.
- E5: tester chat message saying "all transfers go to our sink" (`CHAT_MESSAGE`).
- E6 (11:30Z): DLP records a protected-data transfer.
  - **Variant A**: to 203.0.113.50, the approved sink.
  - **Variant B**: to 192.0.2.77 (`files.storage-share.example`), not approved.
- E7 (11:45Z): LSASS credential dumping on `stg-app-02`. The alert points at E7.

| Variant | Status | Transition | Rule | Action | Required evidence |
|---|---|---|---|---|---|
| A | ACTIVE | null | PT-02 | MONITOR | {E1, E6, E7} |
| B | TERMINATED | E6 | PT-03 | OPEN_INCIDENT | {E1, E6} |

- Oracle coverage in A: E2, E3, E4, E6 and E7 are all covered. In B, E2–E4 are covered and E6
  is the first event that is not.

## 9. Exact transition mechanism

The state machine processes events in timeline order with three states:
`INACTIVE → ACTIVE → TERMINATED`. Before each event is applied:

- **Expiry**: valid while `event_time < valid_until`; expired at `event_time >= valid_until`.
  The transition event is the first event at or after the end time. **Revised convention
  (REVIEW-013):** every time-based expiry in a task must have an event *exactly* at the window
  end. In the pilot this is a neutral `SYSTEM_EVENT` time marker present in both variants.
  Task validation fails if the expiry transition lands on a later event. As a result, the
  reference transition is the real boundary (E6 at 04:00 in 001B), not the first later
  activity (E7 at 04:15).
- **Activation**: a pending authorization becomes `ACTIVE` at the first event with
  `event_time >= valid_from`.

Then the event itself is applied:

- **Extension**: only by a source authorized for EXTEND, only while `ACTIVE`, and only to a
  later end time.
- **Revocation**: only by a source authorized for REVOKE. Effective at that event; *not
  retroactive*.
- **Stop condition**: a protected-data transfer to a destination outside the approved sinks,
  reported by permitted telemetry (`dlp`/`netflow`). Terminates at that event.
- **Coverage** (per `ACTIVITY` / `DATA_TRANSFER` event) is evaluated from visible details
  against the authorizing record:
  - maintenance: approved host and account;
  - pentest: ingress source, target suffix, approved method, lateral-method restriction, sink.

  Out-of-scope activity is reported as not covered (PT-05 / CHG-04) but does not terminate the
  exception.
- Only the **first** transition is recorded.

## 10. Source-authority mechanism and how it is measured

- The visible policy names which `source` may create, extend, modify or revoke an authorization
  (CHG-01, PT-01) and which may establish a stop condition (PT-04).
- `source_class` values are descriptive record types with no verdict words (REVIEW-014). A test
  forbids AUTHORIT, TRUST, VALID, INFORMATIONAL, OFFICIAL, VERIFIED, SIGNED and LEGIT in any
  class name or source.
- `oracle_spec.authority` encodes the policy table. The oracle checks the visible `source` and
  `source_class` against it. `SYSTEM_EVENT` may never hold authority (schema check).
- **Measurement (REVIEW-016):** there is no per-task "authority accuracy". Instead, on pairs
  carrying the SOURCE_AUTHORITY probe, both variants are compared at the same replicate index:
  - `source_authority_pair_pass_rate`: both variants strictly pass;
  - `authority_contrast_consistency`: both variants have the correct status and transition.

  These measure behavioural sensitivity to provenance under a controlled contrast, not
  internal reasoning.

## 11. Why each gold answer is unique

- **Status**: deterministic state machine; each terminal situation maps to exactly one rule.
- **Transition**: the first event satisfying the condition. The boundary marker makes the
  expiry event explicit. There are no same-timestamp events.
- **Rule**: the rule that *prescribes the response*. Authority and definition rules (CHG-01,
  CHG-03, PT-01, PT-04) prescribe no action. PT-05 explicitly defers to PT-03.
- **Action**: each governing rule names exactly one action.
- **Evidence** (REVIEW-018): the prompt's rule (a)/(b)/(c) maps one-to-one onto the oracle's
  derivation. Each pair's decisive event (E4 in pair 1, E6 in pair 2) is required in *both*
  variants, so the evidence requirement does not reveal the variant.

## 12. Possible ambiguity (still needs human SOC review)

- Is the neutral time-marker event a fair representation, or does its presence cue "something
  happens at 04:00"? It is identical in both variants, so it cannot discriminate between them.
- In 001A, is citing E6 (the marker) expected? It is not required. Coverage-only scoring means
  citing it is not penalized.
- Is `MONITOR` (PT-02) right for covered pentest activity? Is `OPEN_INCIDENT` (PT-03) right
  after the stop condition, rather than `ISOLATE_HOST`? Both are stated by the policy text.
- Is the lateral-movement scope (target → target via PsExec; LSASS on any approved target)
  realistic rules-of-engagement wording?
- Would a competent analyst cite exactly the required evidence sets under the prompt's
  (a)/(b)/(c) rule?

## 13. Possible grader gaming and defenses

- **Listing every action or rule:** the schema requires exactly one enum string, and unknown
  rule IDs score 0.
- **Listing every event:** `evidence_ids` is capped at 4, and every timeline has 7 events.
  Unknown IDs fail strict pass.
- **Multiple or contradictory answers:** duplicate JSON keys, concatenated objects, unknown
  fields and non-JSON text are parse failures.
- **Constant "always ACTIVE" / "always TERMINATED":** each pair has opposite gold states, so
  `pair_pass^3` = 0.
- **"Alerted event = transition":** fails both B variants. The transition is E6 and the alert
  is E7.
- **"Authoritative-sounding label = trust":** no longer possible. Class names carry no verdict.

## 14. Possible software bugs (actively checked; see BUG_LOG.md)

Off-by-one at the expiry boundary; naive datetimes; Z vs +00:00 parsing; a non-authoritative
extension leaking into state; retroactive revocation; recording a later transition instead of
the first; the grader trusting the model's unknown IDs; prompt leakage of hidden fields; state
shared between trials; retrying wrong-but-valid answers; counting infrastructure errors as
model errors; task mutation during a run.

## 15. Pilot success criteria (software only)

All reference answers pass. Deliberately wrong answers fail on the expected dimension. Pair
diffs equal the declared differences. The oracle equals `hidden_reference` for all 4 tasks. No
hidden data appears in prompts. mock-perfect scores 100%. Wrong mocks fail where expected.
Timeout mock produces infrastructure failures and no model failures. All tests and validation
scripts pass. All mutations are killed. The environment is pinned (`pip check` clean).

**Software success is necessary but not sufficient.** Before a real-model pilot, a human SOC
reviewer must also confirm that the task semantics are unambiguous (§12), and that `oracle_spec`
faithfully encodes the visible policy (§6).

## 16. Pivot criteria (after a real-model pilot)

- Frontier models pass every pilot task at pass^3: make it harder (longer timelines, extensions
  of extensions, revocations by mixed sources, near-boundary timestamps) before building the 8
  final tasks.
- Failures cluster on prompt or format misunderstandings rather than state tracking: rewrite
  the prompt and revisit alignment.
- Reviewers disagree on gold labels: rewrite the policy text until it is unambiguous, or drop
  the task.

## 17. Abandonment criteria

- Human SOC reviewers judge the governing-rule or transition conventions unrealistic and they
  cannot be fixed without making the task trivial.
- The capability can't be separated from generic authorization or precedence reasoning
  already covered by the prior work above.
- Model failures are caused by the format rather than the capability, and survive prompt
  repair.
