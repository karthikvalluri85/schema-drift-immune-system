"""Blast radius from dbt lineage (manifest.json) + SDIS column maps.

column (RAW) → staging models that read it (column maps) → every downstream model
(manifest child_map) → exposures (dashboards, apps, ML) with their business tier.

Model-level lineage is used on purpose: when a staging model fails or a load is
quarantined, *every* downstream model is affected, not only those naming the column.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

import yaml

from .models import BlastRadius, DriftEvent


LINEAGE_SNAPSHOT = Path(__file__).resolve().parent / "data" / "lineage.json"


def load_manifest(dbt_dir: Path) -> dict[str, Any]:
    return json.loads((dbt_dir / "target" / "manifest.json").read_text())


def slim_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Just the lineage SDIS needs (models, exposures, edges) — committed so demo mode runs without dbt."""
    keep = lambda n: n.startswith(("model.", "exposure.", "source."))  # noqa: E731
    return {
        "metadata": {"project_name": manifest["metadata"].get("project_name")},
        "nodes": {k: {"name": v["name"]} for k, v in sorted(manifest["nodes"].items()) if k.startswith("model.")},
        "child_map": {k: sorted(c for c in v if keep(c)) for k, v in sorted(manifest.get("child_map", {}).items())
                      if keep(k)},
        "exposures": {k: {"name": v["name"], "label": v.get("label"), "type": v.get("type"),
                          "owner": {"name": (v.get("owner") or {}).get("name")},
                          "config": {"meta": {**(v.get("meta") or {}), **((v.get("config") or {}).get("meta") or {})}}}
                      for k, v in sorted(manifest.get("exposures", {}).items())},
    }


def staging_models_reading(dbt_dir: Path, table: str, columns: set[str]) -> list[str]:
    """Staging models whose column map reads any of `columns` from RAW `table`."""
    hits = []
    for mp in sorted((dbt_dir / "sdis_maps").glob("*.yml")):
        m = yaml.safe_load(mp.read_text())
        if m["source"]["table"].upper() != table.upper():
            continue
        used = {str(c.get("source", "")).upper() for c in m["columns"]}
        exprs = " ".join(str(c.get("expr", "")) for c in m["columns"]).upper()
        if used & columns or any(col in exprs for col in columns):
            hits.append(m["model"])
    return hits


def _downstream(manifest: dict[str, Any], start_ids: list[str]) -> tuple[list[str], list[str]]:
    child_map = manifest.get("child_map", {})
    seen: set[str] = set()
    q = deque(start_ids)
    while q:
        node = q.popleft()
        for child in child_map.get(node, []):
            if child not in seen:
                seen.add(child)
                q.append(child)
    models = sorted(n for n in seen if n.startswith("model."))
    exposures = sorted(n for n in seen if n.startswith("exposure."))
    return models, exposures


def tier_for(n_models: int, exposures: list[dict[str, Any]], rules: dict[str, Any]) -> str:
    tier1 = any(int(e.get("tier", 9)) == 1 for e in exposures)
    hi, med = rules["high"], rules["medium"]
    if (hi.get("any_tier1_exposure") and tier1) or n_models >= hi.get("or_min_models", 99):
        return "high"
    if len(exposures) >= med.get("min_exposures", 99) or n_models >= med.get("or_min_models", 99):
        return "medium"
    return "low"


def compute(dbt_dir: Path, table: str, events: list[DriftEvent], rules: dict[str, Any],
            manifest: dict[str, Any] | None = None) -> BlastRadius:
    manifest = manifest or load_manifest(dbt_dir)
    consumed: set[str] = set()
    for e in events:
        if e.drift_class == "additive":
            continue  # a column nobody reads yet has no blast radius
        for c in (e.column_before, e.column_after):
            if c:
                consumed.add(c.upper())
    if not consumed:
        return BlastRadius(columns=[], staging_models=[], downstream_models=[], exposures=[], tier="low")

    stg = staging_models_reading(dbt_dir, table, consumed)
    project = manifest["metadata"].get("project_name") or next(
        (n.split(".")[1] for n in manifest["nodes"] if n.startswith("model.")), "")
    start = [f"model.{project}.{s}" for s in stg]
    models, exposure_ids = _downstream(manifest, start)
    exposures = []
    for eid in exposure_ids:
        ex = manifest["exposures"][eid]
        meta = {**(ex.get("meta") or {}), **((ex.get("config") or {}).get("meta") or {})}
        exposures.append({"name": ex["name"], "label": ex.get("label") or ex["name"], "type": ex.get("type"),
                          "owner": (ex.get("owner") or {}).get("name"), "tier": meta.get("tier", 9),
                          "audience": meta.get("audience")})
    short = [m.split(".")[-1] for m in models]
    return BlastRadius(columns=sorted(consumed), staging_models=stg, downstream_models=short,
                       exposures=exposures, tier=tier_for(len(short) + len(stg), exposures, rules))
