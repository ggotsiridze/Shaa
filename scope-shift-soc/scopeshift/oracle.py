"""Deterministic exception-lifecycle state machine.

The oracle does NOT read hidden_reference. It derives the answer from:
  * the model-visible timeline (timestamps, source, source_class, structured details), and
  * oracle_spec (machine-readable encoding of the visible policy: authority table, which
    claim each event makes, and which rule/action applies in each terminal situation).

States: INACTIVE -> ACTIVE -> TERMINATED (absorbing).

Time semantics (all timestamps timezone-aware, compared as absolute instants):
  * An authorization is ACTIVE while  valid_from <= event_time < valid_until.
  * At event_time >= valid_until it is EXPIRED. The transition event is the first timeline
    event whose timestamp is at or after valid_until.
  * Revocation and stop conditions take effect at their own event and are never retroactive:
    events before them keep the state recorded in `event_states`.
  * Only the first transition to TERMINATED is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from scopeshift.schema import (
    Action,
    Capability,
    EventKind,
    ExceptionStatus,
    OracleSpec,
    RuleOutcome,
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

PROTECTED_CLASSIFICATION = "PROTECTED"


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
    termination_cause: TerminationCause | None
    event_states: dict[str, State]
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


def is_expired(event_time: datetime, valid_until: datetime) -> bool:
    """Boundary rule: valid while event_time < valid_until; expired at event_time >= valid_until."""
    return event_time >= valid_until


def is_authorized(spec: OracleSpec, capability: Capability, event: TimelineEvent) -> bool:
    return any(
        g.capability == capability and g.source == event.source and g.source_class == event.source_class
        for g in spec.authority
    )


class _Machine:
    def __init__(self, spec: OracleSpec, enforce_source_authority: bool) -> None:
        self.spec = spec
        self.enforce = enforce_source_authority
        self.state = State.INACTIVE
        self.valid_from: datetime | None = None
        self.valid_until: datetime | None = None
        self.approved_sinks: set[str] = set()
        self.authorizing_events: list[str] = []
        self.transition_event_id: str | None = None
        self.cause: TerminationCause | None = None
        self.rejected: list[str] = []
        self.kinds = {s.event_id: s.kind for s in spec.event_semantics}

    def _authorized(self, kind: EventKind, event: TimelineEvent) -> bool:
        if not self.enforce:
            return True
        ok = is_authorized(self.spec, KIND_TO_CAPABILITY[kind], event)
        if not ok:
            self.rejected.append(f"{event.event_id}:{kind.value}:unauthorized_source")
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

    def step(self, event: TimelineEvent) -> State:
        self._apply_clock(event)
        kind = self.kinds.get(event.event_id)
        if kind is None or kind == EventKind.SCOPE_ACTIVITY:
            return self.state
        if not self._authorized(kind, event):
            return self.state
        d = event.details
        where = f"{event.event_id}.details"
        if kind == EventKind.AUTHORIZATION_CREATE:
            if self.valid_from is not None or self.state == State.TERMINATED:
                self.rejected.append(f"{event.event_id}:CREATE:authorization_already_exists")
                return self.state
            self.valid_from = parse_instant(_detail(d, "valid_from", where), where)
            self.valid_until = parse_instant(_detail(d, "valid_until", where), where)
            if self.valid_until <= self.valid_from:
                raise ValueError(f"{where}: valid_until must be after valid_from")
            self.approved_sinks = {s.strip() for s in d.get("approved_sinks", "").split(",") if s.strip()}
            self.authorizing_events.append(event.event_id)
            self._apply_clock(event)
        elif kind == EventKind.AUTHORIZATION_EXTEND:
            if self.state != State.ACTIVE:
                self.rejected.append(f"{event.event_id}:EXTEND:not_active")
                return self.state
            new_end = parse_instant(_detail(d, "new_valid_until", where), where)
            if self.valid_until is not None and new_end > self.valid_until:
                self.valid_until = new_end
                self.authorizing_events.append(event.event_id)
        elif kind == EventKind.AUTHORIZATION_REVOKE:
            if self.state == State.ACTIVE:
                self._terminate(event, TerminationCause.REVOKED)
        elif kind == EventKind.DATA_TRANSFER:
            if (
                self.state == State.ACTIVE
                and d.get("data_classification") == PROTECTED_CLASSIFICATION
                and d.get("destination_ip") not in self.approved_sinks
            ):
                self._terminate(event, TerminationCause.STOP_CONDITION)
        return self.state


def _outcome(spec: OracleSpec, state: State, cause: TerminationCause | None) -> RuleOutcome:
    rm = spec.rule_map
    if state == State.ACTIVE:
        return rm.covered
    if state == State.INACTIVE:
        chosen = rm.no_exception
    else:
        chosen = {
            TerminationCause.EXPIRED: rm.expired,
            TerminationCause.REVOKED: rm.revoked,
            TerminationCause.STOP_CONDITION: rm.stop_condition,
        }[cause]  # type: ignore[index]
    if chosen is None:
        raise ValueError(f"oracle_spec.rule_map has no outcome for state={state} cause={cause}")
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
    outcome = _outcome(spec, state, machine.cause)
    anchor = machine.transition_event_id if state == State.TERMINATED else spec.evaluated_event_id
    evidence = list(dict.fromkeys([*machine.authorizing_events, anchor]))
    return OracleResult(
        exception_status=status,
        transition_event_id=machine.transition_event_id,
        governing_rule_id=outcome.rule_id,
        primary_action=outcome.action,
        required_evidence_ids=evidence,
        authorizing_event_ids=list(machine.authorizing_events),
        termination_cause=machine.cause,
        event_states=event_states,
        rejected_claims=machine.rejected,
        effective_window_end=machine.valid_until,
    )


def check_against_reference(task: Task) -> list[str]:
    """Return mismatches between the oracle and hidden_reference. Empty list == consistent."""
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
    return problems
