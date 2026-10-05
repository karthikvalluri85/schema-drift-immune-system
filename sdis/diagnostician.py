"""Diagnostician — deterministic classification of drift into the five classes.

    additive       new column nobody consumes yet
    rename         dropped + added column with the same fingerprint (MINHASH, nulls, cardinality)
    type_widening  lossless type change (NUMBER(10,2) → NUMBER(18,2), VARCHAR(20) → VARCHAR(200))
    breaking       dropped column, narrowing or incompatible type change
    semantic       same name + type, but the meaning moved (unit change, code re-mapping)

No LLM is involved in any of these decisions (invariant: llm-never-routes).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from statistics import median
from typing import Any

from .models import ColumnMeta, ColumnProfile, DriftEvent, SchemaDiff
from .sftypes import compare, parse

SimilarityFn = Callable[[ColumnProfile, ColumnProfile], float]


# ---------------------------------------------------------------------------
# Rename pairing
# ---------------------------------------------------------------------------
def fingerprint_score(old: ColumnProfile, new: ColumnProfile, containment: float,
                      weights: dict[str, float]) -> dict[str, float]:
    """Weighted similarity of two column fingerprints.

    `containment` = share of the new column's distinct values already seen in the old
    column's baseline (MINHASH Jaccard + HLL cardinalities, computed in Snowflake).
    """
    null_sim = 1.0 - min(1.0, abs((old.null_rate or 0.0) - (new.null_rate or 0.0)))
    dc_old, dc_new = max(old.distinct_count or 0, 0), max(new.distinct_count or 0, 0)
    distinct_ratio = (min(dc_old, dc_new) / max(dc_old, dc_new)) if max(dc_old, dc_new) else 1.0
    score = (weights["value_containment"] * containment
             + weights["null_rate"] * null_sim
             + weights["distinct_ratio"] * distinct_ratio)
    return {"score": round(score, 4), "value_containment": round(containment, 4),
            "null_similarity": round(null_sim, 4), "distinct_ratio": round(distinct_ratio, 4)}


def pair_renames(diff: SchemaDiff, old_profiles: dict[str, ColumnProfile], new_profiles: dict[str, ColumnProfile],
                 similarity: SimilarityFn, thresholds: dict[str, Any]) -> tuple[list[tuple[ColumnMeta, ColumnMeta, dict]], list[dict]]:
    """Greedy, deterministic matching of dropped→added columns by fingerprint."""
    candidates: list[tuple[float, str, str, dict]] = []
    rejected: list[dict] = []
    for d in diff.dropped:
        for a in diff.added:
            same_family = parse(d.data_type).family == parse(a.data_type).family
            if not same_family:
                rejected.append({"pair": f"{d.name}→{a.name}", "reason": f"type family {d.data_type} vs {a.data_type}"})
                continue
            op, np_ = old_profiles.get(d.name.upper()), new_profiles.get(a.name.upper())
            if op is None or np_ is None:
                rejected.append({"pair": f"{d.name}→{a.name}", "reason": "missing profile"})
                continue
            # Fail safe: tiny value domains (flags, small ints) overlap by accident, so they
            # are never auto-paired. They fall through to breaking + additive → human review.
            if min(op.distinct_count or 0, np_.distinct_count or 0) < thresholds.get("rename_min_distinct", 50):
                rejected.append({"pair": f"{d.name}→{a.name}",
                                 "reason": f"distinct values below {thresholds.get('rename_min_distinct', 50)}; too small to fingerprint"})
                continue
            fp = fingerprint_score(op, np_, similarity(op, np_), thresholds["rename_weights"])
            if fp["score"] >= thresholds["rename_min_score"]:
                candidates.append((fp["score"], d.name, a.name, fp))
            else:
                rejected.append({"pair": f"{d.name}→{a.name}", "reason": f"score {fp['score']} < {thresholds['rename_min_score']}", **fp})
    # deterministic: best score first, then names
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    used_d, used_a, pairs = set(), set(), []
    by_name_d = {c.name: c for c in diff.dropped}
    by_name_a = {c.name: c for c in diff.added}
    for _, dn, an, fp in candidates:
        if dn in used_d or an in used_a:
            continue
        used_d.add(dn)
        used_a.add(an)
        pairs.append((by_name_d[dn], by_name_a[an], fp))
    return pairs, rejected


# ---------------------------------------------------------------------------
# Semantic drift
# ---------------------------------------------------------------------------
def _robust_z(value: float, baseline: Sequence[float]) -> float:
    med = median(baseline)
    mad = median([abs(x - med) for x in baseline]) or (abs(med) * 0.01) or 1e-9
    return abs(value - med) / (1.4826 * mad)


def _match_factor(ratio: float, known: list[dict[str, Any]]) -> dict[str, Any] | None:
    for k in known:
        f = float(k["factor"])
        for direction, r in (("down", ratio), ("up", 1.0 / ratio if ratio else 0.0)):
            if f and abs(r - f) / f <= float(k["tolerance"]):
                return {"factor": f, "label": k["label"], "direction": direction}
    return None


def numeric_semantic_check(col: str, new: ColumnProfile, baseline: list[ColumnProfile],
                           thresholds: dict[str, Any]) -> dict[str, Any] | None:
    meds = [p.num_median for p in baseline if p.num_median not in (None, 0)]
    if len(meds) < 3 or new.num_median in (None, 0):
        return None
    base_med = median(meds)
    ratio = base_med / new.num_median  # >1 means values got smaller
    factor = max(ratio, 1.0 / ratio)
    z = _robust_z(new.num_median, meds)
    if factor < thresholds["semantic_min_factor"] or z < thresholds["semantic_min_robust_z"]:
        return None
    hypothesis = _match_factor(ratio, thresholds.get("known_factors", []))
    return {
        "check": "numeric_scale_shift",
        "column": col,
        "baseline_median": round(base_med, 4),
        "new_median": round(new.num_median, 4),
        "ratio_baseline_over_new": round(ratio, 4),
        "robust_z": round(z, 1),
        "baseline_loads": [p.load_id for p in baseline],
        "hypothesis": hypothesis,
    }


def psi(expected: dict[str, float], actual: dict[str, float], eps: float = 1e-4) -> float:
    keys = set(expected) | set(actual)
    total = 0.0
    for k in keys:
        e, a = max(expected.get(k, 0.0), eps), max(actual.get(k, 0.0), eps)
        total += (a - e) * math.log(a / e)
    return total


def categorical_semantic_check(col: str, new: ColumnProfile, baseline: list[ColumnProfile],
                               thresholds: dict[str, Any]) -> dict[str, Any] | None:
    base = [p.top_k for p in baseline if p.top_k]
    if len(base) < 3 or not new.top_k:
        return None
    keys = sorted({k for b in base for k in b})
    expected = {k: sum(b.get(k, 0.0) for b in base) / len(base) for k in keys}
    score = psi(expected, new.top_k)
    if score < thresholds["categorical_psi"]:
        return None
    overlap = len(set(expected) & set(new.top_k)) / max(len(set(expected) | set(new.top_k)), 1)
    subtype = "code_meaning_change" if overlap >= thresholds["categorical_domain_overlap"] else "new_value_domain"
    return {"check": "categorical_shift", "column": col, "psi": round(score, 3), "domain_overlap": round(overlap, 3),
            "baseline_distribution": {k: round(v, 3) for k, v in expected.items()},
            "new_distribution": {k: round(v, 3) for k, v in new.top_k.items()}, "subtype": subtype}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def classify(diff: SchemaDiff, load_id: str, old_profiles: dict[str, ColumnProfile],
             new_profiles: dict[str, ColumnProfile], baseline: dict[str, list[ColumnProfile]],
             similarity: SimilarityFn, thresholds: dict[str, Any],
             restatement_evidence: Callable[[str], dict[str, Any] | None] | None = None,
             ) -> tuple[list[DriftEvent], list[dict[str, Any]]]:
    """Return (events, rejected_alternatives). Order of events is deterministic."""
    events: list[DriftEvent] = []
    pairs, rejected = pair_renames(diff, old_profiles, new_profiles, similarity, thresholds)
    paired_d = {d.name for d, _, _ in pairs}
    paired_a = {a.name for _, a, _ in pairs}

    for d, a, fp in pairs:
        type_rel = compare(d.data_type, a.data_type)
        events.append(DriftEvent(
            table=diff.table, load_id=load_id, drift_class="rename", subtype="column_renamed",
            column_before=d.name, column_after=a.name, type_before=d.data_type, type_after=a.data_type,
            confidence=fp["score"], evidence={"fingerprint": fp, "type_relation": type_rel},
        ))

    leftover_added = [a for a in diff.added if a.name not in paired_a]
    for d in diff.dropped:
        if d.name in paired_d:
            continue
        maybe = [
            {"column": a.name, "type": a.data_type,
             "why_not_rename": next((r["reason"] for r in rejected if r["pair"] == f"{d.name}→{a.name}"), "no match")}
            for a in leftover_added
        ]
        events.append(DriftEvent(
            table=diff.table, load_id=load_id, drift_class="breaking", subtype="column_dropped",
            column_before=d.name, type_before=d.data_type, confidence=1.0,
            evidence={"possible_replacements": maybe},
        ))

    for a in leftover_added:
        events.append(DriftEvent(
            table=diff.table, load_id=load_id, drift_class="additive", subtype="column_added",
            column_after=a.name, type_after=a.data_type, confidence=1.0,
            evidence={"nullable": a.nullable},
        ))

    for old, new in diff.type_changed:
        rel = compare(old.data_type, new.data_type)
        cls = "type_widening" if rel in ("widening", "widening_lossy") else "breaking"
        sub = {"widening": "lossless_widening", "widening_lossy": "lossy_widening",
               "narrowing": "type_narrowing"}.get(rel, "type_incompatible")
        events.append(DriftEvent(
            table=diff.table, load_id=load_id, drift_class=cls, subtype=sub,
            column_before=old.name, column_after=new.name, type_before=old.data_type, type_after=new.data_type,
            confidence=1.0, evidence={"type_relation": rel},
        ))

    for col in diff.unchanged:
        name = col.name.upper()
        new_p, base = new_profiles.get(name), baseline.get(name, [])
        if not new_p or not base:
            continue
        fam = parse(col.data_type).family
        finding = None
        if fam == "numeric":
            finding = numeric_semantic_check(name, new_p, base, thresholds)
            if finding:
                finding["subtype"] = "unit_change" if finding.get("hypothesis") else "scale_shift"
                if restatement_evidence:
                    finding["restatement_check"] = restatement_evidence(name)
        elif fam in ("text", "boolean"):
            finding = categorical_semantic_check(name, new_p, base, thresholds)
        if finding:
            events.append(DriftEvent(
                table=diff.table, load_id=load_id, drift_class="semantic", subtype=finding["subtype"],
                column_before=name, column_after=name, type_before=col.data_type, type_after=col.data_type,
                confidence=_semantic_confidence(finding), evidence=finding,
            ))

    events.sort(key=lambda e: (e.drift_class, e.column_before or "", e.column_after or ""))
    return events, rejected


def _semantic_confidence(finding: dict[str, Any]) -> float:
    conf = 0.7
    if finding.get("hypothesis"):
        conf += 0.15
    rc = finding.get("restatement_check") or {}
    if rc.get("matched_rows", 0) >= 30 and rc.get("ratio_cv", 1.0) < 0.05:
        conf += 0.14
    if finding.get("check") == "categorical_shift":
        conf = 0.8 if finding.get("subtype") == "code_meaning_change" else 0.75
    return round(min(conf, 0.99), 2)
