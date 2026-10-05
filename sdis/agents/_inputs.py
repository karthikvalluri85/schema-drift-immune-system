"""Gathers everything the deterministic engine needs for one (table, load) from Snowflake."""
from __future__ import annotations

from typing import Any

from .. import engine
from ..context import Context
from ..models import RouteDecision


def analyze_load(ctx: Context, table: str, key: str | None, load_id: str) -> tuple[RouteDecision, dict[str, Any]]:
    wh, th = ctx.wh, ctx.playbooks["thresholds"]
    before_id = wh.last_passed_snapshot_id(table)
    before = wh.snapshot(table, before_id) if before_id else []
    after = wh.snapshot(table, load_id) or wh.current_schema(table)
    baseline_loads = [x for x in wh.passed_loads(table, th["semantic_baseline_loads"]) if x != load_id]
    baseline = wh.profiles(table, baseline_loads)
    new_profiles = {c: ps[-1] for c, ps in wh.profiles(table, [load_id]).items()}
    old_profiles = {c: ps[-1] for c, ps in baseline.items()}

    def similarity(old, new) -> float:
        return wh.containment(table, old.column, baseline_loads, new.column, load_id)

    def restatement(col: str):
        return wh.restatement_ratio(table, key, col, load_id) if key else None

    d = engine.analyze(table=ctx.settings.raw_fqn(table), load_id=load_id, before=before, after=after,
                       old_profiles=old_profiles, new_profiles=new_profiles, baseline=baseline,
                       similarity=similarity, playbooks=ctx.playbooks, dbt_dir=ctx.settings.dbt_dir,
                       manifest=ctx.manifest(), key_columns={key} if key else set(), restatement=restatement)
    extras = {
        "baseline_top_k": {c: ps[-1].top_k for c, ps in baseline.items() if ps and ps[-1].top_k},
        "new_top_k": {c: p.top_k for c, p in new_profiles.items() if p.top_k},
        "baseline_loads": baseline_loads,
    }
    return d, extras
