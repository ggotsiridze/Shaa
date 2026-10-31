"""Deterministic exception-lifecycle state machine.

What this proves, and what it does not (REVIEW-015):
  Reference answers are validated against a deterministic state machine backed by a
  hand-authored machine-readable encoding of the visible policy (`oracle_spec`: authority
  table, which claim each event makes, scope model, rule outcomes). This catches
  inconsistencies between the stored reference answer and the encoded policy logic, but it
  does not prove that the machine-readable policy specification faithfully represents the
  natural-language policy. That equivalence requires human review.

  Within that limit the oracle evaluates VISIBLE values: event timestamps, `source`,
  `source_class` and structured `details` (windows, hosts, accounts, techniques, destinations).
  The oracle never reads `hidden_reference`.

States: INACTIVE -> ACTIVE -> TERMINATED (absorbing). Only the first transition is recorded.

Time semantics (timezone-aware, compared as instants):
  * ACTIVE while  valid_from <= event_time < valid_until; EXPIRED at event_time >= valid_until.
  * The transition event for expiry is the first timeline event at or after valid_until.
    Task validation additionally requires that event to sit exactly at valid_until (a neutral
    TIME_MARKER), so the transition is the real boundary, not later activity (REVIEW-013).
  * Revocation and stop conditions take effect at their own event; never retroactive.

Coverage: an ACTIVITY/DATA_TRANSFER event is covered iff the exception is ACTIVE at that event
and its visible details satisfy the scope model. Out-of-scope activity does not by itself end
the exception; only expiry, revocation or a stop condition do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from scopeshift.schema import (
    MAX_EVIDENCE_IDS,
    Action,
    Capability,
    EventKind,
    ExceptionStatus,
    OracleSpec,
    RuleOutcome,
    ScopeModel,
    Task,
    TerminationCause,
    TimelineEvent,
)

KIND_TO_CAPABILITY = {
    EventKind.AUTHORIZATION_CREATE: Capability.CREATE,
    EventKind.AUTHORIZATION_EXTEND: Capability.EXTEND,
    EventKind.AUTHORIZATION_REVOKE: Capability.REVOKE,
    EventKind.DATA_TRANSFER: Capability.ESTABLISH_STOP_CONDITION,
}
STATE_CHANGE_KINDS = {EventKind.AUTHORIZATION_CREATE, EventKind.AUTHORIZATION_EXTEND, EventKind.AUTHORIZATION_REVOKE}
PROTECTED_CLASSIFICATION = "PROTECTED"
EXFIL_TECHNIQUE = "exfiltration_simulation"


class State(str, Enum):
    INACTIVE = "INACTIVE"
    ACTIVE = "ACTIVE"
    TERMINATED = "TERMINATED"


@dataclass
class OracleResult:
    exception_status: ExceptionStatus
    transition_event_id: str | None
    governing_rule_id: str
    primary_action: Action
    required_evidence_ids: list[str]
    authorizing_event_ids: list[str]
    rejected_claim_event_ids: list[str]
    last_stop_check_event_id: str | None
    termination_cause: TerminationCause | None
    event_states: dict[str, State]
    event_coverage: dict[str, bool]
    rejected_claims: list[str] = field(default_factory=list)
    effective_window_end: datetime | None = None


def parse_instant(value: str, where: str) -> datetime:
    dt = datetime.fromisoformat(value)  # Python 3.11+ accepts a trailing "Z"
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{where}: timestamp {value!r} is not timezone-aware")
    return dt


def _detail(details: dict[str, str], key: str, where: str) -> str:
    if key not in details:
        raise ValueError(f"{where}: missing required field {key!r}")
    return details[key]


def _csv(value: str) -> set[str]:
    return {v.strip() for v in value.split(",") if v.strip()}


def is_expired(event_time: datetime, valid_until: datetime) -> bool:
    """Boundary rule: valid while event_time < valid_until; expired at event_time >= valid_until."""
    return event_time >= valid_until


def is_authorized(spec: OracleSpec, capability: Capability, event: TimelineEvent) -> bool:
    return any(
        g.capability == capability and g.source == event.source and g.source_class == event.source_class
        for g in spec.authority
    )


@dataclass
class _Scope:
    """Approved scope, read from the visible details of the authorizing record."""

    host: str | None = None
    account: str | None = None
    processes: set[str] = field(default_factory=set)
    ingress_source: str | None = None
    target_suffix: str | None = None
    methods: set[str] = field(default_factory=set)
    lateral_methods: set[str] = field(default_factory=set)
    sinks: set[str] = field(default_factory=set)

    @classmethod
    def from_record(cls, model: ScopeModel, d: dict[str, str], where: str) -> _Scope:
        if model == ScopeModel.MAINTENANCE:
            return cls(host=_detail(d, "host", where), account=_detail(d, "account", where),
                       processes=_csv(_detail(d, "approved_processes", where)))
        return cls(
            ingress_source=_detail(d, "approved_ingress_source", where),
            target_suffix=_detail(d, "approved_target_suffix", where),
            methods=_csv(_detail(d, "approved_methods", where)),
            lateral_methods=_csv(d.get("lateral_methods", "")),
            sinks=_csv(d.get("approved_sinks", "")),
        )

    def in_target_scope(self, host: str | None) -> bool:
        return bool(host) and self.target_suffix is not None and host.endswith(self.target_suffix)  # type: ignore[union-attr]

    def covers(self, model: ScopeModel, kind: EventKind, d: dict[str, str]) -> bool:
        if model == ScopeModel.MAINTENANCE:
            # CHG-02: approved account, on the approved host, matching the approved activity
            # description -- the last read from the event's own `process` (REVIEW-026).
            return d.get("host") == self.host and d.get("account") == self.account and d.get("process") in self.processes
        # PT-02 (2): every covered activity, transfers included, must itself show an approved method.
        # The method is read from the event's own visible `technique`; it is never inferred from
        # the authorization's method list (REVIEW-024). Missing technique -> not covered.
        technique = d.get("technique")
        if technique not in self.methods:
            return False
        if kind == EventKind.DATA_TRANSFER:
            # PT-02 (1)/(3): sent from an approved target. PT-02 (4): protected data only to the sink.
            # E1 also approves exfiltration simulation "to the approved sink only", whatever the data.
            sink_required = d.get("data_classification") == PROTECTED_CLASSIFICATION or technique == EXFIL_TECHNIQUE
            return self.in_target_scope(d.get("src_host")) and (not sink_required or d.get("destination_ip") in self.sinks)
        if "src_ip" in d:  # initial ingress from outside the target environment
            return d["src_ip"] == self.ingress_source and self.in_target_scope(d.get("dst_host"))
        if "src_host" in d:  # movement between hosts: only approved lateral methods, target -> target
            return (technique in self.lateral_methods and self.in_target_scope(d["src_host"])
                    and self.in_target_scope(d.get("dst_host")))
        return self.in_target_scope(d.get("host"))  # action executed on a single approved target


class _Machine:
    def __init__(self, spec: OracleSpec, enforce_source_authority: bool) -> None:
        self.spec = spec
        self.enforce = enforce_source_authority
        self.state = State.INACTIVE
        self.valid_from: datetime | None = None
        self.valid_until: datetime | None = None
        self.scope: _Scope | None = None
        self.authorizing_events: list[str] = []
        self.rejected_claim_events: list[str] = []
        self.last_stop_check: str | None = None
        self.transition_event_id: str | None = None
        self.cause: TerminationCause | None = None
        self.rejected: list[str] = []
        self.coverage: dict[str, bool] = {}
        self.kinds = {s.event_id: s.kind for s in spec.event_semantics}

    def _reject(self, event: TimelineEvent, kind: EventKind, reason: str) -> None:
        self.rejected.append(f"{event.event_id}:{kind.value}:{reason}")
        if kind in STATE_CHANGE_KINDS:
            self.rejected_claim_events.append(event.event_id)

    def _authorized(self, kind: EventKind, event: TimelineEvent) -> bool:
        if not self.enforce:
            return True
        ok = is_authorized(self.spec, KIND_TO_CAPABILITY[kind], event)
        if not ok:
            self._reject(event, kind, "unauthorized_source")
        return ok

    def _terminate(self, event: TimelineEvent, cause: TerminationCause) -> None:
        self.state = State.TERMINATED
        self.transition_event_id = event.event_id
        self.cause = cause

    def _apply_clock(self, event: TimelineEvent) -> None:
        if self.state == State.INACTIVE and self.valid_from is not None and event.timestamp >= self.valid_from:
            self.state = State.ACTIVE
        if self.state == State.ACTIVE and self.valid_until is not None and is_expired(event.timestamp, self.valid_until):
            self._terminate(event, TerminationCause.EXPIRED)

    def _covered(self, kind: EventKind, event: TimelineEvent) -> bool:
        return (self.state == State.ACTIVE and self.scope is not None
                and self.scope.covers(self.spec.scope_model, kind, event.details))

    def step(self, event: TimelineEvent) -> State:
        self._apply_clock(event)
        kind = self.kinds.get(event.event_id)
        if kind is None or kind == EventKind.TIME_MARKER:
            return self.state
        if kind == EventKind.ACTIVITY:
            self.coverage[event.event_id] = self._covered(kind, event)
            return self.state
        if kind == EventKind.DATA_TRANSFER:
            self.coverage[event.event_id] = self._covered(kind, event)
        if not self._authorized(kind, event):
            return self.state
        d = event.details
        where = f"{event.event_id}.details"
        if kind == EventKind.AUTHORIZATION_CREATE:
            if self.valid_from is not None or self.state == State.TERMINATED:
                self._reject(event, kind, "authorization_already_exists")
                return self.state
            self.valid_from = parse_instant(_detail(d, "valid_from", where), where)
            self.valid_until = parse_instant(_detail(d, "valid_until", where), where)
            if self.valid_until <= self.valid_from:
                raise ValueError(f"{where}: valid_until must be after valid_from")
            self.scope = _Scope.from_record(self.spec.scope_model, d, where)
            self.authorizing_events.append(event.event_id)
            self._apply_clock(event)
        elif kind == EventKind.AUTHORIZATION_EXTEND:
            if self.state != State.ACTIVE:
                self._reject(event, kind, "not_active")
                return self.state
            new_end = parse_instant(_detail(d, "new_valid_until", where), where)
            if self.valid_until is not None and new_end > self.valid_until:
                self.valid_until = new_end
                self.authorizing_events.append(event.event_id)
        elif kind == EventKind.AUTHORIZATION_REVOKE:
            if self.state == State.ACTIVE:
                self._terminate(event, TerminationCause.REVOKED)
        elif kind == EventKind.DATA_TRANSFER and self.state == State.ACTIVE and self.scope is not None:
            self.last_stop_check = event.event_id
            if (d.get("data_classification") == PROTECTED_CLASSIFICATION
                    and d.get("destination_ip") not in self.scope.sinks):
                self._terminate(event, TerminationCause.STOP_CONDITION)
        return self.state


def _outcome(spec: OracleSpec, state: State, cause: TerminationCause | None, evaluated_covered: bool) -> RuleOutcome:
    rm = spec.rule_map
    if state == State.ACTIVE:
        chosen = rm.covered if evaluated_covered else rm.out_of_scope
    elif state == State.INACTIVE:
        chosen = rm.no_exception
    else:
        chosen = {
            TerminationCause.EXPIRED: rm.expired,
            TerminationCause.REVOKED: rm.revoked,
            TerminationCause.STOP_CONDITION: rm.stop_condition,
        }[cause]  # type: ignore[index]
    if chosen is None:
        raise ValueError(f"oracle_spec.rule_map has no outcome for state={state} cause={cause} covered={evaluated_covered}")
    return chosen


def run_oracle(task: Task, *, enforce_source_authority: bool = True) -> OracleResult:
    """Evaluate the exception at the END of the timeline."""
    spec = task.oracle_spec
    machine = _Machine(spec, enforce_source_authority)
    event_states: dict[str, State] = {}
    for event in task.model_visible.timeline:
        event_states[event.event_id] = machine.step(event)

    state = machine.state
    status = {
        State.ACTIVE: ExceptionStatus.ACTIVE,
        State.TERMINATED: ExceptionStatus.TERMINATED,
        State.INACTIVE: ExceptionStatus.NOT_APPLICABLE,
    }[state]
    evaluated_covered = machine.coverage.get(spec.evaluated_event_id, True)
    outcome = _outcome(spec, state, machine.cause, evaluated_covered)

    # Required evidence (mirrors the prompt's evidence rule, REVIEW-018):
    # (a) authorization record(s) and permitted changes; (b) state-change claims that did not take
    # effect; (c) TERMINATED: the transition event; otherwise the alerted event plus, when the
    # policy defines a stop condition, the latest event evaluated against it.
    tail: list[str | None]
    if state == State.TERMINATED:
        tail = [machine.transition_event_id]
    else:
        stop_check = machine.last_stop_check if spec.rule_map.stop_condition is not None else None
        tail = [stop_check, spec.evaluated_event_id]
    evidence = list(dict.fromkeys(
        e for e in [*machine.authorizing_events, *machine.rejected_claim_events, *tail] if e is not None))
    return OracleResult(
        exception_status=status,
        transition_event_id=machine.transition_event_id,
        governing_rule_id=outcome.rule_id,
        primary_action=outcome.action,
        required_evidence_ids=evidence,
        authorizing_event_ids=list(machine.authorizing_events),
        rejected_claim_event_ids=list(dict.fromkeys(machine.rejected_claim_events)),
        last_stop_check_event_id=machine.last_stop_check,
        termination_cause=machine.cause,
        event_states=event_states,
        event_coverage=dict(machine.coverage),
        rejected_claims=machine.rejected,
        effective_window_end=machine.valid_until,
    )


def check_against_reference(task: Task) -> list[str]:
    """Return mismatches between the oracle and hidden_reference, plus design-rule violations.

    Empty list == the stored reference is consistent with the encoded policy logic. This does
    NOT validate the encoding against the natural-language policy (human review)."""
    try:
        res = run_oracle(task)
    except (ValueError, KeyError) as exc:
        return [f"{task.task_id}: oracle could not evaluate task: {exc}"]
    ref = task.hidden_reference
    problems = []
    pairs = [
        ("exception_status", res.exception_status, ref.exception_status),
        ("transition_event_id", res.transition_event_id, ref.transition_event_id),
        ("governing_rule_id", res.governing_rule_id, ref.governing_rule_id),
        ("primary_action", res.primary_action, ref.primary_action),
        ("required_evidence_ids", sorted(res.required_evidence_ids), sorted(ref.required_evidence_ids)),
    ]
    for name, got, want in pairs:
        if got != want:
            problems.append(f"{task.task_id}: {name}: oracle={got!r} reference={want!r}")
    if res.termination_cause == TerminationCause.EXPIRED:
        by_id = {e.event_id: e for e in task.model_visible.timeline}
        t = by_id[res.transition_event_id]  # type: ignore[index]
        if t.timestamp != res.effective_window_end:
            problems.append(f"{task.task_id}: expiry transition {t.event_id} is at {t.timestamp.isoformat()}, "
                            f"not at the window end {res.effective_window_end}; add a boundary TIME_MARKER event")
    # Every activity before the transition (or anywhere, if none) must be covered. An uncovered
    # one would trigger CHG-04 / PT-05 escalation on its own, competing with the rule for the
    # alerted event and making the gold action non-unique (REVIEW-025).
    order = [e.event_id for e in task.model_visible.timeline]
    cutoff = order.index(res.transition_event_id) if res.transition_event_id else len(order)
    uncovered = [eid for eid, ok in res.event_coverage.items() if not ok and order.index(eid) < cutoff]
    if uncovered:
        problems.append(f"{task.task_id}: activity {uncovered} is not covered before the transition; "
                        "it creates a competing escalation obligation (ambiguous gold action)")
    if len(res.required_evidence_ids) > MAX_EVIDENCE_IDS:
        problems.append(f"{task.task_id}: derived evidence set exceeds the {MAX_EVIDENCE_IDS}-ID cap: {res.required_evidence_ids}")
    return problems
