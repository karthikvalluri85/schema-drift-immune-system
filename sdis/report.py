"""Human-readable incident summaries shared by PRs, Jira and Paperclip comments."""
from __future__ import annotations

from .models import RouteDecision

SEV_EMOJI = {"SEV1": "🔴", "SEV2": "🟠", "SEV3": "🟡", "SEV4": "🔵", "SEV5": "⚪"}

CLASS_LABEL = {
    "additive": "Additive (new column)",
    "rename": "Rename",
    "type_widening": "Type widening",
    "breaking": "Breaking change",
    "semantic": "Semantic drift",
    "no_drift": "No drift",
}


def primary_event(d: RouteDecision):
    """The event that decided the incident's class (events are sorted by class name, not severity)."""
    return next((e for e in d.events if e.drift_class == d.top_class), d.events[0] if d.events else None)


def headline(d: RouteDecision) -> str:
    e = primary_event(d)
    what = ""
    if e:
        if e.drift_class == "rename":
            what = f"{e.column_before} → {e.column_after}"
        elif e.drift_class == "semantic":
            h = (e.evidence.get("hypothesis") or {}).get("label")
            what = f"{e.column_before} meaning changed" + (f" ({h})" if h else "")
        elif e.type_before and e.type_after and e.type_before != e.type_after:
            what = f"{e.column_before} {e.type_before} → {e.type_after}"
        else:
            what = e.column_before or e.column_after or ""
    table = d.table.split(".")[-1]
    return f"{SEV_EMOJI.get(d.severity, '')} {d.severity} {CLASS_LABEL[d.top_class]} on {table}: {what}".strip()


def title(d: RouteDecision) -> str:
    """Plain one-line title for PRs: `[SEV1] Rename on ORDERS: CUST_ID → CUSTOMER_ID`."""
    return f"[{d.severity}] " + headline(d).split(f"{d.severity} ", 1)[-1]


def markdown(d: RouteDecision, narrative: str | None = None, extra: str | None = None) -> str:
    b = d.blast_radius
    lines = [f"## {headline(d)}", "",
             f"**Incident** `{d.incident_id}` · **Load** `{d.load_id}` · **Route** `{d.route}` · "
             f"**Gate** `{d.gate}` · **Approval** {'required' if d.requires_human_approval else 'not required'} · "
             f"**SLA** {d.sla_minutes} min", ""]
    if narrative:
        lines += ["### What happened (Cortex summary — informational, not used for routing)", narrative, ""]
    lines += ["### Drift events", "| Class | Subtype | Before | After | Confidence |", "|---|---|---|---|---|"]
    for e in d.events:
        before = f"{e.column_before or ''} {e.type_before or ''}".strip()
        after = f"{e.column_after or ''} {e.type_after or ''}".strip()
        lines.append(f"| {e.drift_class} | {e.subtype} | {before or '—'} | {after or '—'} | {e.confidence:.2f} |")
    if b:
        lines += ["", f"### Blast radius: **{b.tier.upper()}**",
                  f"- Staging: {', '.join(b.staging_models) or '—'}",
                  f"- Downstream models ({len(b.downstream_models)}): {', '.join(b.downstream_models) or '—'}"]
        for x in b.exposures:
            lines.append(f"- 📊 **{x['label']}** (tier {x['tier']}, {x.get('audience') or x.get('owner') or ''})")
    lines += ["", "### Deterministic decisions"]
    for dec in d.decisions:
        lines.append(f"- **{dec['decision']}** → `{dec['choice']}` — {dec['reasoning']}")
    if extra:
        lines += ["", extra]
    return "\n".join(lines) + "\n"
