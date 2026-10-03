"""Typed records shared by every agent.

Agents hand work to each other through Snowflake (DRIFT schema) and Paperclip issues,
so everything here is plain data with a stable JSON form.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

DRIFT_CLASSES = ("additive", "rename", "type_widening", "breaking", "semantic")


def stable_hash(*parts: Any, length: int = 12) -> str:
    """Deterministic short id from arbitrary JSON-able parts (invariant: deterministic-routing)."""
    payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:length]


@dataclass(frozen=True)
class ColumnMeta:
    """One column as seen in INFORMATION_SCHEMA at snapshot time."""

    name: str
    data_type: str  # normalised, e.g. NUMBER(10,2), VARCHAR(20), BOOLEAN
    ordinal: int = 0
    nullable: bool = True


@dataclass
class ColumnProfile:
    """Statistical fingerprint of one column in one load."""

    column: str
    load_id: str
    data_type: str = ""
    row_count: int = 0
    null_rate: float = 0.0
    distinct_count: int = 0
    num_mean: float | None = None
    num_median: float | None = None
    num_p05: float | None = None
    num_p95: float | None = None
    num_min: float | None = None
    num_max: float | None = None
    top_k: dict[str, float] | None = None  # value -> share of non-null rows
    minhash: Any = None  # opaque Snowflake MINHASH state (value overlap)
    hll: Any = None  # opaque Snowflake HLL_EXPORT state (cardinality of unions)


@dataclass
class SchemaDiff:
    table: str
    added: list[ColumnMeta] = field(default_factory=list)
    dropped: list[ColumnMeta] = field(default_factory=list)
    type_changed: list[tuple[ColumnMeta, ColumnMeta]] = field(default_factory=list)
    unchanged: list[ColumnMeta] = field(default_factory=list)

    @property
    def has_structural_change(self) -> bool:
        return bool(self.added or self.dropped or self.type_changed)


@dataclass
class DriftEvent:
    table: str
    load_id: str
    drift_class: str
    subtype: str
    column_before: str | None = None
    column_after: str | None = None
    type_before: str | None = None
    type_after: str | None = None
    confidence: float = 1.0
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def event_id(self) -> str:
        return "DE-" + stable_hash(
            self.table, self.load_id, self.drift_class, self.subtype, self.column_before, self.column_after
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["event_id"] = self.event_id
        return d


@dataclass
class BlastRadius:
    columns: list[str]
    staging_models: list[str]
    downstream_models: list[str]
    exposures: list[dict[str, Any]]
    tier: str  # low | medium | high

    @property
    def tier1_exposures(self) -> list[dict[str, Any]]:
        return [e for e in self.exposures if int(e.get("tier", 9)) == 1]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RouteDecision:
    incident_id: str
    table: str
    load_id: str
    top_class: str
    severity: str
    route: str
    gate: str  # PASS | HOLD | QUARANTINE
    surgeon_action: str
    diplomat_action: str
    requires_human_approval: bool
    auto_merge_when_green: bool
    sla_minutes: int
    events: list[DriftEvent]
    blast_radius: BlastRadius | None
    decisions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["events"] = [e.to_dict() for e in self.events]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RouteDecision:
        d = dict(d)
        d["events"] = [DriftEvent(**{k: v for k, v in e.items() if k != "event_id"}) for e in d.get("events", [])]
        br = d.get("blast_radius")
        d["blast_radius"] = BlastRadius(**br) if br else None
        return cls(**d)


@dataclass
class AgentOutput:
    """Every agent run returns one of these (invariant: every-decision-traced).

    Shape follows the typed sub-agent output pattern from the AWS ADOP sample:
    status + artifacts + decisions[] with reasoning and rejected alternatives.
    """

    agent: str
    run_id: str
    status: str  # success | blocked | failed | noop
    incident_id: str | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decision(what: str, choice: Any, reasoning: str, alternatives: list[str] | None = None,
             confidence: str = "high") -> dict[str, Any]:
    """Cognitive-trace entry: what was decided, why, and what was rejected."""
    return {
        "decision": what,
        "choice": choice,
        "reasoning": reasoning,
        "alternatives_rejected": alternatives or [],
        "confidence": confidence,
    }
