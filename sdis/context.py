"""Shared runtime context for agents.

The same agent handlers run in two modes:
  * paperclip  — woken by a Paperclip heartbeat; delegation = child issues; approvals = request_confirmation
  * local      — `sdis run`: a synchronous pipeline for dev/CI/demo; delegation = an in-process queue;
                 approvals come from CLI flags (or an interactive prompt)
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from .config import Settings
from .github import GitHub
from .jira import Jira
from .paperclip import Paperclip
from .policy import load_playbooks
from .trace import Tracer


@dataclass
class Task:
    agent: str
    title: str
    description: str
    incident_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    issue_id: str | None = None  # Paperclip issue id when running under Paperclip


@dataclass
class Context:
    settings: Settings
    agent: str
    mode: str = "local"  # local | paperclip
    approve: bool | None = None  # local mode: True/False = pre-answered, None = ask interactively
    confirm_producer: bool | None = None
    queue: deque = field(default_factory=deque)
    log: list[str] = field(default_factory=list)

    @cached_property
    def tracer(self) -> Tracer:
        return Tracer(self.settings.repo_dir / self.settings.trace_path, run_id=self.settings.paperclip_run_id,
                      agent=self.agent)

    @cached_property
    def wh(self):
        from .warehouse import Warehouse
        return Warehouse(self.settings, self.agent)

    @cached_property
    def paperclip(self) -> Paperclip:
        return Paperclip(self.settings)

    @cached_property
    def github(self) -> GitHub:
        return GitHub(self.settings)

    @cached_property
    def jira(self) -> Jira:
        return Jira(self.settings)

    @cached_property
    def playbooks(self) -> dict[str, Any]:
        return load_playbooks(str(self.settings.policies_dir / "playbooks.yaml"))

    # ------------------------------------------------------------------ dbt metadata
    def watched_tables(self) -> list[dict[str, Any]]:
        doc = yaml.safe_load((self.settings.dbt_dir / "models" / "staging" / "_sources.yml").read_text())
        out = []
        for src in doc["sources"]:
            for t in src.get("tables", []):
                meta = {**(t.get("meta") or {}), **((t.get("config") or {}).get("meta") or {})}
                if meta.get("sdis_watch"):
                    out.append({"table": (t.get("identifier") or t["name"]).upper(), "key": meta.get("sdis_key"),
                                "owner": meta.get("producer_contact") or meta.get("owner")})
        return out

    def owner_of(self, table: str) -> str | None:
        short = table.split(".")[-1].upper()
        return next((t["owner"] for t in self.watched_tables() if t["table"] == short), None)

    def manifest(self, dbt_dir: Path | None = None) -> dict[str, Any] | None:
        import json
        p = (dbt_dir or self.settings.dbt_dir) / "target" / "manifest.json"
        return json.loads(p.read_text()) if p.exists() else None

    # ------------------------------------------------------------------ delegation + approvals
    def say(self, msg: str) -> None:
        self.log.append(msg)
        if self.mode == "local":
            print(msg, flush=True)

    def delegate(self, task: Task, parent_issue_id: str | None = None, goal_id: str | None = None,
                 priority: str = "high") -> str | None:
        marker = f"\n\n---\nsdis-incident: {task.incident_id}" if task.incident_id else ""
        for k, v in task.meta.items():
            marker += f"\nsdis-{k}: {v}"
        if self.mode == "paperclip":
            issue = self.paperclip.create_issue(task.title, task.description + marker, task.agent,
                                                parent_issue_id, goal_id, priority)
            return issue.get("id")
        self.queue.append(task)
        return None

    def approval(self, issue_id: str | None, key: str, prompt: str, kind: str = "approve") -> str:
        """Returns 'accepted' | 'rejected' | 'pending'."""
        if self.mode == "paperclip" and issue_id:
            state = self.paperclip.confirmation_state(issue_id, key)
            if state is None:
                self.paperclip.request_confirmation(issue_id, prompt, key)
                return "pending"
            return {"accepted": "accepted", "approved": "accepted", "rejected": "rejected"}.get(state, "pending")
        if self.settings.approval_channel == "github":
            # Serverless: nobody is at a terminal. A human approves by merging the PR (SEV1/SEV2 PRs are
            # labelled needs-human and never auto-merged); a producer confirms via the sdis-producer workflow.
            preset = self.confirm_producer if kind == "producer" else None
            if preset is None:
                self.say(f"   ⏸️  waiting for a human on GitHub: {prompt}")
                return "pending"
        else:
            preset = self.confirm_producer if kind == "producer" else self.approve
        if preset is not None:
            self.say(f"   🧑 {prompt}  →  {'yes' if preset else 'no'} ({self.settings.approval_channel})")
            return "accepted" if preset else "rejected"
        answer = input(f"\n   🧑 {prompt} [y/N] ").strip().lower()
        return "accepted" if answer in ("y", "yes") else "rejected"
