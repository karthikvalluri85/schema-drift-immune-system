"""Runs agents either under a Paperclip heartbeat or as a local synchronous pipeline."""
from __future__ import annotations

import re
import traceback
from typing import Any

from . import invariants
from .agents import HANDLERS
from .agents.sentinel import bootstrap
from .config import Settings
from .context import Context, Task
from .models import AgentOutput

STATUS_TO_PAPERCLIP = {"success": "done", "noop": "done", "waiting": "in_progress", "blocked": "blocked",
                       "failed": "blocked"}
_META = re.compile(r"^sdis-([a-z_]+):\s*(.+)$", re.M)


def task_from_issue(agent: str, issue: dict[str, Any]) -> Task:
    desc = issue.get("description") or ""
    meta = {k: v.strip() for k, v in _META.findall(desc)}
    return Task(agent=agent, title=issue.get("title", ""), description=desc, incident_id=meta.pop("incident", None),
                meta=meta, issue_id=issue.get("id"))


def render_output(out: AgentOutput) -> str:
    lines = [f"**{out.agent}** → `{out.status}`" + (f" · incident `{out.incident_id}`" if out.incident_id else "")]
    for a in out.artifacts:
        if a.get("url"):
            lines.append(f"- {a['type']}: {a['url']}")
        elif a.get("name"):
            lines.append(f"- {a['type']}: `{a['name']}`")
    lines.append("\n**Decisions**")
    for dec in out.decisions:
        lines.append(f"- {dec['decision']} → `{dec['choice']}` — {dec['reasoning']}")
    if out.warnings:
        lines.append("\n**Warnings**\n" + "\n".join(f"- {w}" for w in out.warnings))
    if out.next_steps:
        lines.append("\n**Next**\n" + "\n".join(f"- {n}" for n in out.next_steps))
    return "\n".join(lines)


def _finish(ctx: Context, out: AgentOutput) -> AgentOutput:
    invariants.check_agent_output(out)
    ctx.tracer.output(out)
    return out


# ---------------------------------------------------------------------------- Paperclip heartbeat
def heartbeat(agent: str, settings: Settings | None = None) -> int:
    s = settings or Settings()
    ctx = Context(settings=s, agent=agent, mode="paperclip")
    pc = ctx.paperclip
    if not pc.enabled:
        raise SystemExit("PAPERCLIP_API_URL / PAPERCLIP_API_KEY not set — run under a Paperclip heartbeat or use `sdis run`")
    me = pc.me()
    mode = invariants.budget_mode(int(me.get("spentMonthlyCents") or 0), int(me.get("budgetMonthlyCents") or 0))
    if mode == "paused":
        return 0
    issues = pc.assignments()
    order = {"in_progress": 0, "in_review": 1, "todo": 2, "blocked": 3}
    issues.sort(key=lambda i: (0 if i.get("id") == s.paperclip_task_id else 1, order.get(i.get("status"), 9)))
    handled = 0
    for issue in issues:
        if mode == "critical_only" and not re.search(r"SEV[12]|🔴|🟠", issue.get("title", "")) and agent != "sentinel":
            ctx.tracer.emit("operational", "skipped_budget", None, issue=issue.get("id"))
            continue
        try:
            pc.checkout(issue["id"])
        except Exception as exc:  # 409 Conflict → someone else owns it; never retry
            ctx.tracer.emit("operational", "checkout_skipped", None, issue=issue.get("id"), reason=repr(exc))
            continue
        task = task_from_issue(agent, issue)
        try:
            out = HANDLERS[agent](ctx, task)
            _finish(ctx, out)
            pc.update(issue["id"], status=STATUS_TO_PAPERCLIP[out.status], comment=render_output(out),
                      unblock_action="; ".join(out.next_steps + out.warnings) or None)
        except Exception as exc:
            ctx.tracer.emit("operational", "agent_error", task.incident_id, error=repr(exc))
            pc.update(issue["id"], status="blocked",
                      comment=f"**{agent}** failed: `{type(exc).__name__}: {exc}`\n\n```\n{traceback.format_exc()[-1500:]}\n```",
                      unblock_action=f"Fix {type(exc).__name__}: {exc}")
        handled += 1
    return handled


# ---------------------------------------------------------------------------- serverless events
_BRANCH = re.compile(r"^(?:refs/heads/)?sdis/(inc-[0-9a-f]{8})-", re.I)


def incident_from_branch(ref: str) -> str | None:
    m = _BRANCH.match(ref or "")
    return m.group(1).upper() if m else None


def run_task(task: Task, settings: Settings | None = None, approve: bool | None = None,
             confirm: bool | None = None) -> list[AgentOutput]:
    """Run one agent task, then everything it delegates (used by the GitHub Actions event workflows)."""
    s = settings or Settings()
    root = Context(settings=s, agent=task.agent, approve=approve, confirm_producer=confirm)
    root.queue.append(task)
    outputs: list[AgentOutput] = []
    while root.queue:
        t: Task = root.queue.popleft()
        ctx = Context(settings=s, agent=t.agent, approve=approve, confirm_producer=confirm, queue=root.queue)
        ctx.__dict__["wh"] = root.wh
        ctx.wh.agent = t.agent
        outputs.append(_finish(ctx, HANDLERS[t.agent](ctx, t)))
    return outputs


# ---------------------------------------------------------------------------- local pipeline
def run_local(settings: Settings | None = None, approve: bool | None = None, confirm: bool | None = None,
              do_bootstrap: bool = False) -> list[AgentOutput]:
    """Sentinel → Diagnostician → Surgeon/Diplomat → Auditor, synchronously (for dev, CI and demos)."""
    s = settings or Settings()
    outputs: list[AgentOutput] = []
    root = Context(settings=s, agent="sentinel", approve=approve, confirm_producer=confirm)
    if do_bootstrap:
        outputs.append(_finish(root, bootstrap(root)))
        return outputs
    outputs.append(_finish(root, HANDLERS["sentinel"](root, None)))
    queue = root.queue
    while queue:
        task: Task = queue.popleft()
        ctx = Context(settings=s, agent=task.agent, approve=approve, confirm_producer=confirm, queue=queue)
        ctx.__dict__["wh"] = root.wh  # share one Snowflake session
        ctx.wh.agent = task.agent
        out = _finish(ctx, HANDLERS[task.agent](ctx, task))
        outputs.append(out)
        if out.status == "waiting" and task.agent == "surgeon" and approve:
            queue.append(task)  # CI pending: re-check once more in local mode
            break
    return outputs
