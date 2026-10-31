"""Pydantic models for tasks, the oracle spec and the model answer."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

EVENT_ID_PATTERN = r"^E[1-9][0-9]*$"
MAX_EVIDENCE_IDS = 4
MAX_RATIONALE_CHARS = 2000  # rationale is not scored; v0.4 raised from 600 (pilot finding)


class SourceClass(str, Enum):
    """Descriptive record types. Deliberately verdict-free (no AUTHORITATIVE/TRUSTED/...):
    which sources may change authorization state is stated only in the visible policy text
    and encoded, hidden, in oracle_spec.authority (REVIEW-014)."""

    CHANGE_RECORD = "CHANGE_RECORD"
    ENGAGEMENT_RECORD = "ENGAGEMENT_RECORD"
    TICKET_RECORD = "TICKET_RECORD"
    CHAT_MESSAGE = "CHAT_MESSAGE"
    EMAIL_MESSAGE = "EMAIL_MESSAGE"
    ENDPOINT_TELEMETRY = "ENDPOINT_TELEMETRY"
    NETWORK_TELEMETRY = "NETWORK_TELEMETRY"
    DLP_TELEMETRY = "DLP_TELEMETRY"
    SYSTEM_EVENT = "SYSTEM_EVENT"


# Source classes that may never hold authority over authorization state (validated in Task).
NON_AUTHORITY_SOURCE_CLASSES = frozenset({SourceClass.SYSTEM_EVENT})


class ExceptionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    TERMINATED = "TERMINATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Action(str, Enum):
    DOCUMENT = "DOCUMENT"
    MONITOR = "MONITOR"
    ESCALATE_TIER2 = "ESCALATE_TIER2"
    OPEN_INCIDENT = "OPEN_INCIDENT"
    ISOLATE_HOST = "ISOLATE_HOST"
    REVOKE_SESSION = "REVOKE_SESSION"


class Capability(str, Enum):
    CREATE = "CREATE"
    EXTEND = "EXTEND"
    REVOKE = "REVOKE"
    ESTABLISH_STOP_CONDITION = "ESTABLISH_STOP_CONDITION"


class EventKind(str, Enum):
    """Semantic claim an event makes. Whether the claim takes effect is decided by the oracle."""

    AUTHORIZATION_CREATE = "AUTHORIZATION_CREATE"
    AUTHORIZATION_EXTEND = "AUTHORIZATION_EXTEND"
    AUTHORIZATION_REVOKE = "AUTHORIZATION_REVOKE"
    DATA_TRANSFER = "DATA_TRANSFER"
    ACTIVITY = "ACTIVITY"  # coverage is evaluated from visible details, not assumed
    TIME_MARKER = "TIME_MARKER"  # neutral clock event; never changes state by itself


class ScopeModel(str, Enum):
    MAINTENANCE = "MAINTENANCE"
    PENTEST = "PENTEST"


class TerminationCause(str, Enum):
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    STOP_CONDITION = "STOP_CONDITION"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- model-visible


class PolicyRule(Strict):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class OrganizationPolicy(Strict):
    rules: list[PolicyRule] = Field(min_length=1)

    @field_validator("rules")
    @classmethod
    def unique_rule_ids(cls, rules: list[PolicyRule]) -> list[PolicyRule]:
        ids = [r.id for r in rules]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate rule ids: {ids}")
        return rules


class Alert(Strict):
    source: str
    rule_name: str
    timestamp: AwareDatetime
    related_event_id: str = Field(pattern=EVENT_ID_PATTERN)


class AssetContext(Strict):
    hostname: str
    environment: str
    criticality: str


class TimelineEvent(Strict):
    event_id: str = Field(pattern=EVENT_ID_PATTERN)
    timestamp: AwareDatetime
    source: str = Field(min_length=1)
    source_class: SourceClass
    event: str = Field(min_length=1)
    details: dict[str, str] = Field(default_factory=dict)


class ModelVisible(Strict):
    organization_policy: OrganizationPolicy
    alert: Alert
    asset_context: AssetContext
    timeline: list[TimelineEvent] = Field(min_length=1)

    @model_validator(mode="after")
    def check_timeline(self) -> ModelVisible:
        ids = [e.event_id for e in self.timeline]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate event ids: {ids}")
        for prev, cur in zip(self.timeline, self.timeline[1:]):
            if cur.timestamp < prev.timestamp:
                raise ValueError(f"timeline not chronological at {cur.event_id}")
        by_id = {e.event_id: e for e in self.timeline}
        related = by_id.get(self.alert.related_event_id)
        if related is None:
            raise ValueError("alert.related_event_id not in timeline")
        if related.timestamp != self.alert.timestamp:
            raise ValueError("alert.timestamp differs from its related event")
        return self


# ---------------------------------------------------------------- hidden


class HiddenReference(Strict):
    exception_status: ExceptionStatus
    transition_event_id: str | None = Field(default=None, pattern=EVENT_ID_PATTERN)
    governing_rule_id: str
    primary_action: Action
    required_evidence_ids: list[str] = Field(min_length=1, max_length=MAX_EVIDENCE_IDS)


class AuthorityGrant(Strict):
    capability: Capability
    source: str
    source_class: SourceClass


class EventSemantics(Strict):
    event_id: str = Field(pattern=EVENT_ID_PATTERN)
    kind: EventKind


class RuleOutcome(Strict):
    rule_id: str
    action: Action


class RuleMap(Strict):
    covered: RuleOutcome
    expired: RuleOutcome | None = None
    revoked: RuleOutcome | None = None
    stop_condition: RuleOutcome | None = None
    out_of_scope: RuleOutcome | None = None
    no_exception: RuleOutcome | None = None


class OracleSpec(Strict):
    exception_id: str
    scope_model: ScopeModel
    authority: list[AuthorityGrant] = Field(min_length=1)
    event_semantics: list[EventSemantics]
    rule_map: RuleMap
    evaluated_event_id: str = Field(pattern=EVENT_ID_PATTERN)
    probes: list[str] = Field(default_factory=list)


class InternalMetadata(Strict):
    pair_id: str
    variant: Literal["A", "B"]
    synthetic: Literal[True]
    human_review_status: Literal["PENDING", "REVIEWED"]


class Task(Strict):
    task_id: str = Field(pattern=r"^SS-[A-Z]+-[0-9]{3}[A-Z]$")
    model_visible: ModelVisible
    hidden_reference: HiddenReference
    oracle_spec: OracleSpec
    internal_metadata: InternalMetadata

    @model_validator(mode="after")
    def cross_references(self) -> Task:
        event_ids = {e.event_id for e in self.model_visible.timeline}
        rule_ids = {r.id for r in self.model_visible.organization_policy.rules}
        ref = self.hidden_reference
        if ref.transition_event_id is not None and ref.transition_event_id not in event_ids:
            raise ValueError("hidden_reference.transition_event_id not in timeline")
        missing = set(ref.required_evidence_ids) - event_ids
        if missing:
            raise ValueError(f"required_evidence_ids not in timeline: {sorted(missing)}")
        if len(set(ref.required_evidence_ids)) != len(ref.required_evidence_ids):
            raise ValueError("duplicate required_evidence_ids")
        if ref.governing_rule_id not in rule_ids:
            raise ValueError("hidden_reference.governing_rule_id not a policy rule")
        if (ref.exception_status == ExceptionStatus.TERMINATED) != (ref.transition_event_id is not None):
            raise ValueError("transition_event_id must be set iff status is TERMINATED")
        spec = self.oracle_spec
        if spec.evaluated_event_id not in event_ids:
            raise ValueError("oracle_spec.evaluated_event_id not in timeline")
        if spec.evaluated_event_id != self.model_visible.alert.related_event_id:
            raise ValueError("oracle_spec.evaluated_event_id must be the alerted event")
        for sem in spec.event_semantics:
            if sem.event_id not in event_ids:
                raise ValueError(f"oracle_spec semantics references unknown {sem.event_id}")
        for grant in spec.authority:
            if grant.source_class in NON_AUTHORITY_SOURCE_CLASSES:
                raise ValueError(f"{grant.source_class.value} may not hold authority ({grant.capability.value})")
        for name in ("covered", "expired", "revoked", "stop_condition", "out_of_scope", "no_exception"):
            outcome = getattr(spec.rule_map, name)
            if outcome is not None and outcome.rule_id not in rule_ids:
                raise ValueError(f"rule_map.{name} references unknown rule {outcome.rule_id}")
        if not self.task_id.endswith(self.internal_metadata.variant):
            raise ValueError("task_id suffix must equal internal_metadata.variant")
        return self


# ---------------------------------------------------------------- model answer

StatusLiteral = Literal["ACTIVE", "TERMINATED", "NOT_APPLICABLE"]
ActionLiteral = Literal[
    "DOCUMENT", "MONITOR", "ESCALATE_TIER2", "OPEN_INCIDENT", "ISOLATE_HOST", "REVOKE_SESSION"
]


class ModelAnswer(BaseModel):
    """Strict output contract. strict=True: no type coercion (e.g. list -> str, 1 -> "1")."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    exception_status: StatusLiteral
    transition_event_id: str | None = Field(pattern=EVENT_ID_PATTERN)
    # No format pattern: the prompt only asks for "the id of the single policy rule"; the grader
    # scores unknown ids as wrong instead of rejecting them as unparseable.
    governing_rule_id: str = Field(min_length=1)
    primary_action: ActionLiteral
    evidence_ids: list[str] = Field(min_length=1, max_length=MAX_EVIDENCE_IDS)
    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_CHARS)

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_valid(cls, ids: list[str]) -> list[str]:
        import re

        for i in ids:
            if not re.fullmatch(EVENT_ID_PATTERN, i):
                raise ValueError(f"bad event id {i!r}")
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate evidence_ids")
        return ids
