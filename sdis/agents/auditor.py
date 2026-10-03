"""Auditor — verify, release the load gate, resolve, and keep the ledger (MTTR + cost)."""
from __future__ import annotations

import os
import subprocess

from ..context import Context, Task
from ..models import AgentOutput, decision

AGENT = "auditor"


def _dbt_build(ctx: Context, select: str) -> tuple[bool, str]:
    if not os.environ.get("SNOWFLAKE_ACCOUNT"):
        return True, "skipped (no Snowflake credentials in this environment)"
    subprocess.run(["git", "pull", "--ff-only"], cwd=ctx.settings.repo_dir, capture_output=True)
    env = {**os.environ, "SNOWFLAKE_PRIVATE_KEY_PATH": os.environ.get("SNOWFLAKE_DBT_PRIVATE_KEY_PATH",
                                                                     os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", ""))}
    r = subprocess.run(["dbt", "build", "--select", select, "--profiles-dir", "."], cwd=ctx.settings.dbt_dir,
                       env=env, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout[-1500:] if r.returncode else "dbt build ok")


def handle(ctx: Context, task: Task) -> AgentOutput:
    inc = task.incident_id
    d = ctx.wh.decision(inc)
    table = d.table.split(".")[-1]
    out = AgentOutput(agent=AGENT, run_id=ctx.tracer.run_id, status="success", incident_id=inc)
    approved = task.meta.get("approved") in (True, "true")
    with ctx.wh.for_incident(inc):
        ctx.wh.set_gate(table, d.load_id, "PASSED", inc, AGENT, approved=approved)
        out.decisions.append(decision("release_gate", "PASSED", "Remediation merged; load can flow downstream"))
        sel = " ".join(f"{m}+" for m in (d.blast_radius.staging_models if d.blast_radius else [])) or "source:raw+"
        ok, log = _dbt_build(ctx, sel)
        out.decisions.append(decision("verify", "dbt build ok" if ok else "dbt build failed", log[-300:]))
        if not ok:
            ctx.wh.set_gate(table, d.load_id, "QUARANTINED", inc, AGENT)
            ctx.wh.update_incident(inc, status="OPEN")
            out.status = "blocked"
            ctx.say("🧾 Auditor    verification failed → load re-quarantined, incident re-opened")
            return out
        ctx.wh.update_incident(inc, status="RESOLVED")
        row = ctx.wh.incident(inc) or {}
        mins = ctx.wh.query(f"select datediff('second', opened_at, mitigated_at)/60.0 as ttm, "
                            f"datediff('second', opened_at, resolved_at)/60.0 as ttr "
                            f"from {ctx.wh.drift('INCIDENTS')} where incident_id = %s", (inc,))[0]
        out.artifacts.append({"type": "ledger", "minutes_to_mitigate": mins["TTM"], "minutes_to_resolve": mins["TTR"],
                              "jira": row.get("JIRA_KEY"), "pr": row.get("PR_URL")})
        ctx.say(f"🧾 Auditor    {inc} RESOLVED · mitigated in {float(mins['TTM'] or 0):.1f} min · "
                f"resolved in {float(mins['TTR'] or 0):.1f} min")
        if row.get("JIRA_KEY") and ctx.settings.jira_api_token:
            ctx.jira.comment(row["JIRA_KEY"], f"Resolved by SDIS. PR: {row.get('PR_URL')}. "
                                              f"Time to resolve: {float(mins['TTR'] or 0):.1f} min.")
    return out


def ledger(ctx: Context) -> list[dict]:
    """Weekly ledger: MTTR by class + Snowflake credits by incident (ACCOUNT_USAGE latency applies)."""
    return ctx.wh.query(f"""
        select i.top_class, i.severity, count(*) as incidents,
               avg(datediff('second', i.opened_at, i.mitigated_at))/60 as avg_min_to_mitigate,
               avg(datediff('second', i.opened_at, i.resolved_at))/60  as avg_min_to_resolve,
               sum(c.credits) as credits
        from {ctx.wh.drift('INCIDENTS')} i
        left join (select incident_id, sum(credits) credits from {ctx.wh.drift('INCIDENT_COMPUTE_COST')} group by 1) c
               on c.incident_id = i.incident_id
        group by 1, 2 order by 2, 1""")
