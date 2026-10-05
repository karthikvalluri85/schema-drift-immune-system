"""Diplomat — humans in the loop: Jira ticket, producer note, and producer confirmation."""
from __future__ import annotations

from ..context import Context, Task
from ..jira import producer_note
from ..models import AgentOutput, decision
from ..report import headline, markdown, primary_event

AGENT = "diplomat"


def handle(ctx: Context, task: Task) -> AgentOutput:
    inc = task.incident_id
    d = ctx.wh.decision(inc)
    row = ctx.wh.incident(inc) or {}
    out = AgentOutput(agent=AGENT, run_id=ctx.tracer.run_id, status="success", incident_id=inc)
    owner = ctx.owner_of(d.table)
    note = producer_note(d, owner)

    if d.diplomat_action == "fyi_comment":
        out.decisions.append(decision("notify", "fyi_comment", f"{d.severity} {d.top_class}: FYI only, no ticket"))
        out.artifacts.append({"type": "comment", "text": note})
        ctx.say(f"📣 Diplomat   FYI to {owner}: {note}")
        return out

    with ctx.wh.for_incident(inc):
        key = row.get("JIRA_KEY") or ctx.jira.find_existing(inc)
        if not key:
            key = ctx.jira.create_issue(d, row.get("NARRATIVE"), row.get("PR_URL"), owner)
            ctx.wh.update_incident(inc, jira_key=key)
            ctx.say(f"📣 Diplomat   Jira {key} created → {ctx.jira.browse_url(key)}")
        out.artifacts.append({"type": "jira", "key": key, "url": ctx.jira.browse_url(key)})
        out.decisions.append(decision("notify", d.diplomat_action, f"{d.severity}: ticket + note to {owner}"))

    if d.diplomat_action == "jira_and_producer_confirmation":
        e = primary_event(d)
        hyp = (e.evidence.get("hypothesis") or {}).get("label", "a unit change")
        state = ctx.approval(task.issue_id, f"producer-confirm-{inc}",
                             f"Producer ({owner}) confirms {e.column_before} is now {hyp.split(' ')[0]}"
                             f" from load {d.load_id}?", kind="producer")
        out.decisions.append(decision("producer_confirmation", state,
                                      "Semantic fixes are applied only after the producer confirms the new meaning"))
        if state == "pending":
            out.status = "waiting"
            return out
        if state == "rejected":
            out.status = "blocked"
            out.next_steps.append("Producer rejected the hypothesis; load stays quarantined for manual triage")
            return out
        ctx.jira.comment(key, f"Producer confirmed: {hyp}. Surgeon will normalise loads ≥ {d.load_id}.")
        ctx.delegate(Task(agent="surgeon", title=f"Normalise {inc}: {headline(d)}", description=markdown(d),
                          incident_id=inc, meta={"confirmed": "true"}), parent_issue_id=task.issue_id,
                     priority="critical")
        out.next_steps.append("Surgeon: normalize_after_confirmation")
    return out
