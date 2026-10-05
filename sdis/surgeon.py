"""Surgeon — turns a RouteDecision into a minimal, reviewable dbt change.

Every patch edits *mappings* (dbt/sdis_maps/*.yml) and source docs, then re-renders the
generated staging models. Aliases never change, so downstream marts never notice.
All functions here are pure filesystem edits; git/GitHub lives in github.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import codegen
from .models import DriftEvent, RouteDecision
from .sftypes import parse


@dataclass
class Patch:
    action: str
    files: list[str] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    draft: bool = False
    needs_review_notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.files


def _dbt_type(t: str) -> str:
    """Snowflake type → the lower-case spelling used in dbt contracts and maps."""
    s = parse(t)
    if s.base == "NUMBER":
        return f"number({s.precision},{s.scale})"
    if s.base == "VARCHAR":
        return f"varchar({s.length})" if s.length and s.length != 16777216 else "varchar"
    return s.base.lower()


def _maps_for(dbt_dir: Path, table: str) -> list[tuple[Path, dict[str, Any]]]:
    out = []
    for mp in sorted((dbt_dir / "sdis_maps").glob("*.yml")):
        m = yaml.safe_load(mp.read_text(encoding="utf-8"))
        if m["source"]["table"].upper() == table.upper():
            out.append((mp, m))
    return out


def _sources(dbt_dir: Path) -> tuple[Path, dict[str, Any]]:
    p = dbt_dir / "models" / "staging" / "_sources.yml"
    return p, yaml.safe_load(p.read_text(encoding="utf-8"))


def _source_table(doc: dict[str, Any], table: str) -> dict[str, Any]:
    for src in doc["sources"]:
        for t in src.get("tables", []):
            if (t.get("identifier") or t["name"]).upper() == table.upper():
                return t
    raise KeyError(table)


def _dump(path: Path, doc: dict[str, Any], header: str | None = None) -> None:
    body = yaml.safe_dump(doc, sort_keys=False, width=120)
    path.write_text((header + "\n" if header else "") + body, encoding="utf-8", newline="\n")


def _map_header(path: Path) -> str:
    return "\n".join(ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("#"))


# ---------------------------------------------------------------------------
# Patch builders (one per playbook surgeon action)
# ---------------------------------------------------------------------------
def add_source_column(dbt_dir: Path, d: RouteDecision) -> Patch:
    sp, sdoc = _sources(dbt_dir)
    t = _source_table(sdoc, d.table.split(".")[-1])
    patch = Patch("add_source_column")
    for e in d.events:
        if e.drift_class != "additive":
            continue
        if any(c["name"].upper() == e.column_after.upper() for c in t["columns"]):
            continue
        t["columns"].append({"name": e.column_after, "data_type": _dbt_type(e.type_after),
                             "description": f"Added by producer in load {d.load_id}; not yet consumed",
                             "config": {"meta": {"sdis": "new_unused", "sdis_incident": d.incident_id}}})
        patch.summary.append(f"Documented new column `{e.column_after}` ({e.type_after}) in sources.yml")
    if patch.summary:
        _dump(sp, sdoc)
        patch.files.append(str(sp))
    return patch


def update_contract_type(dbt_dir: Path, d: RouteDecision) -> Patch:
    patch = Patch("update_contract_type")
    table = d.table.split(".")[-1]
    sp, sdoc = _sources(dbt_dir)
    t = _source_table(sdoc, table)
    for e in d.events:
        if e.drift_class != "type_widening":
            continue
        new_t = _dbt_type(e.type_after)
        for c in t["columns"]:
            if c["name"].upper() == e.column_before.upper():
                c["data_type"] = new_t
        for mp, m in _maps_for(dbt_dir, table):
            for c in m["columns"]:
                if str(c.get("source", "")).upper() == e.column_before.upper() and c["type"] != new_t:
                    old = c["type"]
                    c["type"] = new_t
                    _dump(mp, m, _map_header(mp))
                    patch.files.append(str(mp))
                    patch.summary.append(f"`{m['model']}.{c['alias']}` contract {old} → {new_t}")
    if patch.summary:
        _dump(sp, sdoc)
        patch.files.append(str(sp))
    return patch


def remap_source_column(dbt_dir: Path, d: RouteDecision) -> Patch:
    patch = Patch("remap_source_column")
    table = d.table.split(".")[-1]
    sp, sdoc = _sources(dbt_dir)
    t = _source_table(sdoc, table)
    for e in d.events:
        if e.drift_class != "rename":
            continue
        for c in t["columns"]:
            if c["name"].upper() == e.column_before.upper():
                c["name"] = e.column_after
                c.setdefault("config", {}).setdefault("meta", {})["sdis_renamed_from"] = e.column_before
        for mp, m in _maps_for(dbt_dir, table):
            changed = False
            for c in m["columns"]:
                if str(c.get("source", "")).upper() == e.column_before.upper():
                    c["source"] = e.column_after
                    changed = True
                    patch.summary.append(
                        f"`{m['model']}.{c['alias']}` now reads `{e.column_after}` (was `{e.column_before}`); "
                        f"alias unchanged → 0 downstream edits")
            if changed:
                _dump(mp, m, _map_header(mp))
                patch.files.append(str(mp))
    if patch.files:
        _dump(sp, sdoc)
        patch.files.append(str(sp))
    return patch


def _share_mapping(old_top: dict[str, float], new_top: dict[str, float]) -> list[tuple[str, str]]:
    """Pair values by rank of their share (deterministic; ties broken by value)."""
    o = sorted(old_top.items(), key=lambda kv: (-kv[1], kv[0]))
    n = sorted(new_top.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(nv, ov) for (nv, _), (ov, _) in zip(n, o, strict=False)]


def propose_breaking_remap(dbt_dir: Path, d: RouteDecision, profiles: dict[str, Any] | None = None) -> Patch:
    """For a dropped column with a plausible replacement, keep the contract and propose a mapping."""
    patch = Patch("propose_breaking_remap", draft=True)
    table = d.table.split(".")[-1]
    profiles = profiles or {}
    added = [e for e in d.events if e.drift_class == "additive"]
    for e in d.events:
        if not (e.drift_class == "breaking" and e.subtype == "column_dropped"):
            continue
        old_top = (profiles.get("baseline_top_k") or {}).get(e.column_before.upper())
        for repl in added:
            new_top = (profiles.get("new_top_k") or {}).get(repl.column_after.upper())
            if not (old_top and new_top) or len(old_top) != len(new_top):
                continue
            pairs = _share_mapping(old_top, new_top)
            is_bool = parse(repl.type_after).base == "BOOLEAN"
            whens = " ".join(
                (f"when {'' if nv.lower() == 'true' else 'not '}{repl.column_after} then '{ov}'" if is_bool
                 else f"when {repl.column_after} = '{nv}' then '{ov}'") for nv, ov in pairs)
            expr = f"case {whens} end"
            for mp, m in _maps_for(dbt_dir, table):
                for c in m["columns"]:
                    if str(c.get("source", "")).upper() == e.column_before.upper():
                        c.pop("source", None)
                        c["expr"] = expr
                        c["description"] = (c.get("description", "") + f" [SDIS proposal {d.incident_id}: derived from "
                                            f"{repl.column_after} by value-share matching — confirm with producer]").strip()
                        _dump(mp, m, _map_header(mp))
                        patch.files.append(str(mp))
                        patch.summary.append(f"`{m['model']}.{c['alias']}` derived from `{repl.column_after}`: `{expr}`")
            patch.needs_review_notes.append(
                f"Mapping {pairs} was inferred from value shares "
                f"(baseline {old_top} vs new {new_top}). Confirm semantics with the producer before merging.")
            patch.needs_review_notes.append(
                f"Loads before {d.load_id} lost `{e.column_before}` in RAW. Backfill from the Time Travel clone "
                f"`DRIFT.{table}_PRE_{d.incident_id.replace('-', '_')}` if history must be preserved.")
            break
    if patch.files:
        sp, sdoc = _sources(dbt_dir)
        t = _source_table(sdoc, table)
        for repl in added:
            if not any(c["name"].upper() == repl.column_after.upper() for c in t["columns"]):
                t["columns"].append({"name": repl.column_after, "data_type": _dbt_type(repl.type_after)})
        t["columns"] = [c for c in t["columns"] if not any(
            ev.drift_class == "breaking" and ev.subtype == "column_dropped" and c["name"].upper() == ev.column_before.upper()
            for ev in d.events)]
        _dump(sp, sdoc)
        patch.files.append(str(sp))
    else:
        patch.needs_review_notes.append("No safe replacement mapping found; human remediation required.")
    return patch


def normalize_after_confirmation(dbt_dir: Path, d: RouteDecision, confirmed: bool = False) -> Patch:
    """Semantic unit change: only after the producer confirms, convert new loads back to the contract unit."""
    patch = Patch("normalize_after_confirmation")
    if not confirmed:
        patch.needs_review_notes.append("Waiting for producer confirmation of the unit change.")
        return patch
    table = d.table.split(".")[-1]
    for e in d.events:
        if e.drift_class != "semantic" or e.subtype != "unit_change":
            continue
        h = e.evidence.get("hypothesis") or {}
        if h.get("factor") != 84.0:
            patch.needs_review_notes.append(f"No automatic normaliser for '{h.get('label')}'.")
            continue
        for mp, m in _maps_for(dbt_dir, table):
            for c in m["columns"]:
                if str(c.get("source", "")).upper() == e.column_before.upper():
                    src = c.pop("source")
                    c["expr"] = (f"iff(_LOAD_ID >= '{d.load_id}', {src} * {{{{ var('fx_inr_per_usd') }}}}, {src})")
                    _dump(mp, m, _map_header(mp))
                    patch.files.append(str(mp))
                    patch.summary.append(f"`{m['model']}.{c['alias']}`: loads ≥ {d.load_id} arrive in USD → "
                                         f"× fx_inr_per_usd (contract stays INR)")
    return patch


BUILDERS = {
    "add_source_column": add_source_column,
    "update_contract_type": update_contract_type,
    "remap_source_column": remap_source_column,
    "propose_breaking_remap": propose_breaking_remap,
    "normalize_after_confirmation": normalize_after_confirmation,
}


def build_patch(dbt_dir: Path, d: RouteDecision, **kw: Any) -> Patch:
    if d.surgeon_action in ("none", None):
        return Patch("none")
    fn = BUILDERS[d.surgeon_action]
    patch = fn(dbt_dir, d, **kw) if kw else fn(dbt_dir, d)
    if patch.files:
        regenerated = codegen.run(dbt_dir)
        patch.files.extend(p for p in regenerated if p not in patch.files)
    return patch


def branch_name(d: RouteDecision) -> str:
    return f"sdis/{d.incident_id.lower()}-{d.top_class.replace('_', '-')}"


def events_of(d: RouteDecision, cls: str) -> list[DriftEvent]:
    return [e for e in d.events if e.drift_class == cls]
