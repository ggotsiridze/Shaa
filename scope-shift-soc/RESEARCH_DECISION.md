# RESEARCH_DECISION — ScopeShift-SOC (development pilot)

Status: **pilot design, pre-model-testing**. No model results exist yet. No human or expert
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
through a timeline. The scored target is the *lifecycle state at the end of the timeline* plus
the *exact event at which it changed*, the *rule that governs after the change* and the *action
that rule requires*. The policy explicitly says which source classes may change state. The
grading reference is produced by a deterministic state machine that reads the same visible
fields the model sees. It does not come from hand-written labels.

## 7. Pilot Pair 1 — source authority / maintenance expiry (DEV-PAIR-001)

- CMS record E1 approves CHG-104 for `app-db-02.corp.example` (account `svc-patch`,
  window 03:00Z–04:00Z).
- E2/E3: PowerShell and installer activity inside the window (covered).
- E4 at 03:40Z: text "CHG-104 extended until 04:30 UTC", with a structured
  `new_window_end = 04:30Z`.
  - **Variant A**: E4 comes from `change_management` / `AUTHORITATIVE_POLICY_SOURCE`.
  - **Variant B**: identical text, from `ticketing` / `INTERNAL_INFORMATIONAL_SOURCE`
    (a ticket comment).
- E5: informational ticket comment about progress (both variants).
- E6 at 04:15Z: same PowerShell/installer pattern; the alert points at E6.
- Gold A: `ACTIVE`, transition `null`, rule `CHG-02`, `DOCUMENT`, evidence {E1, E4, E6}.
- Gold B: `TERMINATED`, transition `E6`, rule `CHG-04`, `ESCALATE_TIER2`, evidence {E1, E6}.
- Probes: SOURCE_AUTHORITY_CONFUSION, STALE_AUTHORIZATION.

## 8. Pilot Pair 2 — penetration-test stop condition (DEV-PAIR-002)

- RoE record E1 (Security Engagement Register) approves PT-2026-031: tester source
  198.51.100.23, targets `*.stg.corp.example`, methods including password spraying, PsExec
  lateral movement, LSASS credential dumping and exfiltration *simulation to the approved sink
  203.0.113.50 only*. Window 09:00Z–17:00Z. A stop condition (PT-03) is defined.
- E2–E4: aggressive but in-scope activity (spraying, PsExec, protected test data sent to the
  approved sink).
- E5: tester chat message saying "all transfers go to our sink" (USER_STATEMENT, both variants).
- E6 at 11:30Z: DLP records a protected-data transfer.
  - **Variant A**: destination 203.0.113.50 (approved sink).
  - **Variant B**: destination 192.0.2.77 (`files.storage-share.example`, not approved).
- E7 at 11:45Z: LSASS access on a staging host; the alert points at E7.
- Gold A: `ACTIVE`, `null`, `PT-02`, `MONITOR`, evidence {E1, E7}.
- Gold B: `TERMINATED`, `E6`, `PT-03`, `OPEN_INCIDENT`, evidence {E1, E6}.
- Probes: PREMATURE_TERMINATION (A) vs MISSED_TERMINATION (B). The alert in B sits one event
  after the transition, so "the alerted event is the transition" is wrong.

## 9. Exact transition mechanism

The state machine (`scopeshift/oracle.py`) processes events in timeline order with three states:
`INACTIVE → ACTIVE → TERMINATED`. Before each event is applied:

- **Expiry**: an `ACTIVE` exception is valid while `event_time < valid_until`. At
  `event_time >= valid_until` it is `TERMINATED` (cause `EXPIRED`), and the **transition event
  is the first timeline event whose timestamp is at or after the end time**.
- **Activation**: a pending authorization becomes `ACTIVE` at the first event with
  `event_time >= valid_from`.

Then the event itself is applied:

- **Extension**: only by a source authorized for EXTEND, only while `ACTIVE`, and only if
  recorded before the current end (an expired window cannot be revived).
- **Revocation**: only by a source authorized for REVOKE. Effective at the event; *not
  retroactive*. Earlier events keep the state they were evaluated under (`event_states`).
- **Stop condition**: a protected-data transfer to a destination outside the approved sinks,
  observed by a source authorized to establish stop conditions. Terminates at that event.
- Only the **first** transition is recorded. TERMINATED is absorbing.

**Design decision to review:** the brief suggested "the authoritative expiration event" as the
Pair 1B transition. In practice a CMS rarely writes an "expired" record, and adding one only to
variant B would add a second difference to the pair and an easy cue. So time-based expiry uses
"first event at or after the end time" (E6 in 001B). The model prompt states this convention
explicitly.

## 10. Source-authority mechanism

- The visible policy names which `(source, source_class)` may CREATE / EXTEND / REVOKE
  authorizations and which may ESTABLISH_STOP_CONDITION.
- `oracle_spec.authority` encodes the same table. The oracle checks the *visible* event's
  `source` and `source_class` against it. So flipping a visible source field in the task
  changes the oracle's output, and the validator can detect a mismatch with `hidden_reference`.
- A claimed state change from an unauthorized source is recorded as `rejected_claims` and has
  no effect.

## 11. Why each gold answer is unique

- Status: the state machine is deterministic and the policy gives exactly one rule per terminal
  situation (covered / expired / revoked / stop condition).
- Transition: "first event satisfying the condition" picks one event. Ties are broken by
  timeline order, and the timelines contain no same-timestamp events.
- Rule: each situation maps to one rule ID. The authority rules (CHG-01, PT-04) and the window
  definition rule (CHG-03) describe conditions; they do not prescribe actions. The rule
  *prescribing the response* is the one requested.
- Action: each governing rule names exactly one action from the enum.

## 12. Possible ambiguity (needs human SOC review)

- "Governing rule" could be read as the authority rule (CHG-01) in 001B, because CHG-01 is
  *why* the comment is ignored. The prompt defines governing rule as the one that prescribes
  the required action at the end of the timeline. Reviewers should confirm this is clear.
- The transition convention for time-based expiry (§9).
- In 002A, `MONITOR` vs `DOCUMENT` for covered pentest activity is set by policy text PT-02. A
  real SOC might choose differently, but the policy is explicit.
- In 002B, some analysts would argue for `ISOLATE_HOST` too. The policy names `OPEN_INCIDENT`
  as the required primary action.
- Required-evidence sets are minimal supporting sets. A reasonable analyst might cite E3 in
  001B (the rejected comment). Evidence scoring is coverage-only (extra valid IDs are allowed
  up to the cap), so this is not penalized.

## 13. Possible grader gaming and defenses

- Listing every action or rule: the schema needs exactly one enum string, and unknown rule IDs
  score 0.
- Listing every event: `evidence_ids` is capped at 4, and every timeline has 6–7 events.
  Unknown IDs fail strict pass.
- Multiple contradictory answers: duplicate JSON keys, concatenated objects, unknown fields and
  non-JSON preambles are all parse failures.
- "Always ACTIVE" / "always TERMINATED": each pair has opposite gold states, so a constant
  policy passes at most one variant and `pair_pass_at_3` is 0.
- "Alerted event = transition": fails 002B.

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
scripts pass.

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
