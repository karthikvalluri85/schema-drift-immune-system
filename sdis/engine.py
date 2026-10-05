"""The deterministic core: snapshot diff → classification → blast radius → route.

Pure function of its inputs. Warehouse IO is injected (similarity + restatement callbacks),
so the exact same code path runs in unit tests and against Snowflake.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import blast_radius, diagnostician, sentinel
from .models import ColumnMeta, ColumnProfile, RouteDecision, decision
from .router import route as route_incident


def analyze(*, table: str, load_id: str, before: list[ColumnMeta], after: list[ColumnMeta],
            old_profiles: dict[str, ColumnProfile], new_profiles: dict[str, ColumnProfile],
            baseline: dict[str, list[ColumnProfile]],
            similarity: Callable[[ColumnProfile, ColumnProfile], float],
            playbooks: dict[str, Any], dbt_dir: Path, manifest: dict[str, Any] | None = None,
            key_columns: set[str] | None = None,
            restatement: Callable[[str], dict[str, Any] | None] | None = None) -> RouteDecision:
    diff = sentinel.diff_schemas(table, before, after)
    # identifiers and keys grow monotonically; their medians are not a semantic signal
    key_columns = {k.upper() for k in (key_columns or set())}
    baseline_for_semantics = {k: v for k, v in baseline.items() if k.upper() not in key_columns}
    events, rejected = diagnostician.classify(
        diff, load_id, old_profiles, new_profiles, baseline_for_semantics, similarity,
        playbooks["thresholds"], restatement,
    )
    table_short = table.split(".")[-1]
    blast = blast_radius.compute(dbt_dir, table_short, events, playbooks["blast_radius"], manifest) if events else None
    decision_out = route_incident(table, load_id, events, blast, playbooks)
    decision_out.decisions.insert(0, decision(
        "schema_diff",
        {"added": [c.name for c in diff.added], "dropped": [c.name for c in diff.dropped],
         "type_changed": [f"{o.name}:{o.data_type}→{n.data_type}" for o, n in diff.type_changed]},
        "INFORMATION_SCHEMA snapshot of the previous PASSED load vs the new load",
    ))
    for e in events:
        decision_out.decisions.insert(1, decision(
            f"classify:{e.column_before or e.column_after}", f"{e.drift_class}/{e.subtype}",
            _why(e), alternatives=[f"{r['pair']}: {r['reason']}" for r in rejected
                                   if (e.column_before or "") in r["pair"] or (e.column_after or "") in r["pair"]],
            confidence="high" if e.confidence >= 0.9 else "medium"))
    return decision_out


def _why(e) -> str:
    ev = e.evidence
    if e.drift_class == "rename":
        fp = ev["fingerprint"]
        return (f"{e.column_before}→{e.column_after}: value containment {fp['value_containment']}, "
                f"null similarity {fp['null_similarity']}, distinct ratio {fp['distinct_ratio']} → score {fp['score']}")
    if e.drift_class == "semantic" and ev.get("check") == "numeric_scale_shift":
        h = ev.get("hypothesis") or {}
        return (f"median {ev['baseline_median']} → {ev['new_median']} (×{ev['ratio_baseline_over_new']}, "
                f"robust z={ev['robust_z']}); hypothesis: {h.get('label', 'unknown scale change')}")
    if e.drift_class == "semantic":
        return f"categorical PSI {ev['psi']} with domain overlap {ev['domain_overlap']}"
    if e.drift_class == "breaking" and e.subtype == "column_dropped":
        reps = ev.get("possible_replacements") or []
        return (f"{e.column_before} disappeared; "
                + ("candidates rejected: " + "; ".join(f"{r['column']} ({r['why_not_rename']})" for r in reps)
                   if reps else "no replacement column"))
    if e.type_before and e.type_after:
        return f"{e.type_before} → {e.type_after} is {ev.get('type_relation')}"
    return f"new column {e.column_after} ({e.type_after})"
