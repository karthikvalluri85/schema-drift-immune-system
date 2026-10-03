"""Sentinel — 'something changed'. Snapshots + profiles each new Bronze load.

Clean loads (no drift events) are passed straight through the gate with a recorded
decision; anything else becomes a Diagnostician task. Sentinel never writes RAW.
"""
from __future__ import annotations

from ..context import Context, Task
from ..models import AgentOutput, decision
from ._inputs import analyze_load

AGENT = "sentinel"


def bootstrap(ctx: Context) -> AgentOutput:
    """First run: every existing load becomes the PASSED baseline."""
    out = AgentOutput(agent=AGENT, run_id=ctx.tracer.run_id, status="success")
    for t in ctx.watched_tables():
        cols = ctx.wh.current_schema(t["table"])
        loads = ctx.wh.new_loads(t["table"])
        for ld in loads:
            ctx.wh.save_snapshot(t["table"], ld["LOAD_ID"], cols, ctx.tracer.run_id)
            ctx.wh.profile_load(t["table"], ld["LOAD_ID"], cols, ctx.tracer.run_id)
            ctx.wh.set_gate(t["table"], ld["LOAD_ID"], "PASSED", None, "sentinel:bootstrap", ld["ROW_COUNT"])
        out.decisions.append(decision(f"bootstrap:{t['table']}", f"{len(loads)} loads PASSED",
                                      "No prior baseline; existing loads are trusted as the reference contract"))
        ctx.say(f"🛰️  Sentinel   baseline {t['table']}: {len(loads)} loads profiled and PASSED")
    return out


def handle(ctx: Context, task: Task | None = None) -> AgentOutput:
    out = AgentOutput(agent=AGENT, run_id=ctx.tracer.run_id, status="noop")
    for t in ctx.watched_tables():
        table, key = t["table"], t["key"]
        new = ctx.wh.new_loads(table)
        if not new:
            continue
        cols = ctx.wh.current_schema(table)
        for ld in new:
            load_id = ld["LOAD_ID"]
            ctx.wh.save_snapshot(table, load_id, cols, ctx.tracer.run_id)
            ctx.wh.set_gate(table, load_id, "PENDING", None, AGENT, ld["ROW_COUNT"])
            ctx.wh.profile_load(table, load_id, cols, ctx.tracer.run_id)
            d, _ = analyze_load(ctx, table, key, load_id)
            out.status = "success"
            if not d.events:
                ctx.wh.set_gate(table, load_id, "PASSED", None, AGENT)
                out.decisions.append(decision(f"gate:{table}/{load_id}", "PASSED",
                                              "No structural or semantic drift against the PASSED baseline"))
                ctx.say(f"🛰️  Sentinel   {table}/{load_id}: clean → PASSED")
                continue
            classes = sorted({e.drift_class for e in d.events})
            out.decisions.append(decision(f"escalate:{table}/{load_id}", "diagnostician",
                                          f"Drift signals {classes}; load held PENDING until diagnosed",
                                          alternatives=["auto-pass (rejected: drift present)"]))
            ctx.say(f"🛰️  Sentinel   {table}/{load_id}: drift signals {classes} → load held, Diagnostician paged")
            ctx.delegate(Task(agent="diagnostician", title=f"Diagnose drift on {table} load {load_id}",
                              description=(f"Sentinel saw {len(d.events)} drift signal(s) on `{table}` load "
                                           f"`{load_id}`: {classes}. Classify, route, gate."),
                              incident_id=d.incident_id, meta={"table": table, "load": load_id, "key": key or ""}),
                         parent_issue_id=task.issue_id if task else None)
            out.incident_id = d.incident_id
            out.next_steps.append(f"Diagnostician: {d.incident_id}")
    if out.status == "noop":
        out.decisions.append(decision("scan", "no new loads", "Every RAW load already has a gate decision"))
    return out
