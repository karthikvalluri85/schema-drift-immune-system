"""Router — maps (drift events, blast radius) → one incident route. Pure and deterministic.

Inputs are DriftEvents (from the Diagnostician) and a BlastRadius (from dbt lineage).
Nothing produced by an LLM is accepted here (invariant: llm-never-routes).
"""
from __future__ import annotations

from typing import Any

from . import invariants
from .models import BlastRadius, DriftEvent, RouteDecision, decision, stable_hash

GATE_TO_STATUS = {"PASS": "PASSED", "HOLD": "PENDING", "QUARANTINE": "QUARANTINED"}


def incident_id_for(table: str, load_id: str, events: list[DriftEvent]) -> str:
    return "INC-" + stable_hash(table, load_id, sorted(e.event_id for e in events), length=8).upper()


def top_class(events: list[DriftEvent], precedence: list[str]) -> str:
    if not events:
        return "no_drift"
    present = {e.drift_class for e in events}
    return next(c for c in precedence if c in present)


def route(table: str, load_id: str, events: list[DriftEvent], blast: BlastRadius | None,
          playbooks: dict[str, Any]) -> RouteDecision:
    invariants.check_route_inputs({"table": table, "load_id": load_id, "events": events, "blast": blast})
    cls = top_class(events, playbooks["class_precedence"])
    pb = playbooks["playbooks"][cls]
    tier = blast.tier if blast else "low"
    severity = playbooks["severity"][cls][tier] if cls != "no_drift" else "SEV5"
    needs_human = severity in playbooks["human_approval_severities"]
    auto_merge = bool(pb["auto_merge_when_green"]) and not needs_human
    inc = incident_id_for(table, load_id, events) if events else f"OK-{stable_hash(table, load_id, length=8).upper()}"

    decisions = [
        decision("top_class", cls,
                 f"Highest-precedence class among {sorted({e.drift_class for e in events}) or ['none']} "
                 f"using precedence {playbooks['class_precedence']}"),
        decision("severity", severity, f"severity[{cls}][{tier}] from playbooks.yaml; blast tier '{tier}' "
                 f"({len(blast.downstream_models) if blast else 0} downstream models, "
                 f"{len(blast.exposures) if blast else 0} exposures)"),
        decision("gate", pb["gate"], f"playbook '{pb['route']}' gate for class {cls}",
                 alternatives=[g for g in ("PASS", "HOLD", "QUARANTINE") if g != pb["gate"]]),
        decision("human_approval", needs_human,
                 f"{severity} {'is' if needs_human else 'is not'} in human_approval_severities "
                 f"{playbooks['human_approval_severities']}"),
    ]
    return RouteDecision(
        incident_id=inc, table=table, load_id=load_id, top_class=cls, severity=severity, route=pb["route"],
        gate=pb["gate"], surgeon_action=pb["surgeon"], diplomat_action=pb["diplomat"],
        requires_human_approval=needs_human, auto_merge_when_green=auto_merge,
        sla_minutes=int(pb["sla_minutes"]), events=events, blast_radius=blast, decisions=decisions,
    )
