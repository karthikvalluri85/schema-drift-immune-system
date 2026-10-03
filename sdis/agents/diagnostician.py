"""Diagnostician — 'what changed, how bad, and what happens next'. Fully deterministic.

1. Re-runs the engine for the load and asserts it matches what Sentinel saw (deterministic-routing).
2. Persists the incident + events + full decision in DRIFT.
3. Applies the playbook gate (PASS / HOLD / QUARANTINE). For breaking drift, preserves a
   Time Travel zero-copy clone of RAW as it was before the change.
4. Only then asks Snowflake Cortex for a narrative (llm-never-routes).
5. Delegates to Surgeon and/or Diplomat according to the playbook.
"""
from __future__ import annotations

from .. import invariants
from ..context import Context, Task
from ..models import AgentOutput, decision
from ..report import headline, markdown
from ..router import GATE_TO_STATUS
from ._inputs import analyze_load

AGENT = "diagnostician"


def handle(ctx: Context, task: Task) -> AgentOutput:
    table, load_id, key = task.meta["table"], task.meta["load"], task.meta.get("key") or None
    d, extras = analyze_load(ctx, table, key, load_id)
    if task.incident_id:
        invariants.check_deterministic({"incident_id": task.incident_id}, {"incident_id": d.incident_id})
    out = AgentOutput(agent=AGENT, run_id=ctx.tracer.run_id, status="success", incident_id=d.incident_id,
                      decisions=list(d.decisions))

    with ctx.wh.for_incident(d.incident_id):
        ctx.wh.save_incident(d)
        status = GATE_TO_STATUS[d.gate]
        ctx.wh.set_gate(table, load_id, status, d.incident_id, AGENT)
        ctx.say(f"🩺 Diagnostician {headline(d)}")
        ctx.say(f"   route={d.route} · gate={status} · blast={d.blast_radius.tier if d.blast_radius else 'low'} "
                f"({len(d.blast_radius.downstream_models) if d.blast_radius else 0} models, "
                f"{len(d.blast_radius.exposures) if d.blast_radius else 0} dashboards) · "
                f"approval={'human' if d.requires_human_approval else 'policy'}")
        if d.gate in ("HOLD", "QUARANTINE"):
            ctx.wh.update_incident(d.incident_id, status="MITIGATED")  # bad data can no longer reach marts
        if d.top_class == "breaking":
            prev = extras["baseline_loads"][0] if extras["baseline_loads"] else None
            if prev:
                ts = ctx.wh.query(f"select max(_loaded_at)::varchar as ts from {ctx.settings.raw_fqn(table)} "
                                  f"where _load_id = %s", (prev,))[0]["TS"]
                clone = ctx.wh.preserve_pre_drift_clone(table, d.incident_id, ts)
                out.artifacts.append({"type": "time_travel_clone", "name": clone})
                out.decisions.append(decision("preserve_evidence", clone,
                                              "Dropped column data is recoverable only via Time Travel; "
                                              "zero-copy clone into DRIFT costs ~nothing and keeps RAW untouched"))
                ctx.say(f"   🧊 Time Travel clone preserved: {clone}")

        narrative = ctx.wh.cortex_narrative(d)  # AFTER routing, informational only
        if narrative:
            ctx.wh.update_incident(d.incident_id, narrative=narrative)
            out.artifacts.append({"type": "narrative", "text": narrative})

    parent = task.issue_id
    body = markdown(d, narrative)
    if d.surgeon_action not in ("none", "normalize_after_confirmation"):
        ctx.delegate(Task(agent="surgeon", title=f"Remediate {d.incident_id}: {headline(d)}", description=body,
                          incident_id=d.incident_id), parent_issue_id=parent,
                     priority="critical" if d.severity in ("SEV1", "SEV2") else "medium")
        out.next_steps.append(f"Surgeon: {d.surgeon_action}")
    if d.diplomat_action != "none":
        ctx.delegate(Task(agent="diplomat", title=f"Notify {d.incident_id}: {headline(d)}", description=body,
                          incident_id=d.incident_id), parent_issue_id=parent,
                     priority="critical" if d.severity in ("SEV1", "SEV2") else "low")
        out.next_steps.append(f"Diplomat: {d.diplomat_action}")
    return out
