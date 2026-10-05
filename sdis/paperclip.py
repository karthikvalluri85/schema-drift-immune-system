"""Minimal Paperclip control-plane client implementing the heartbeat protocol.

me → (budget check) → assignments → checkout → work → comment/status → delegate.
See docs/guides/agent-developer/heartbeat-protocol.md in paperclipai/paperclip.
"""
from __future__ import annotations

import re
from typing import Any

import requests

from .config import Settings

INCIDENT_MARKER = re.compile(r"sdis-incident:\s*(INC-[0-9A-F]{8})")


class Conflict(Exception):
    """409 on checkout — another agent owns the task. Never retry."""


class Paperclip:
    def __init__(self, s: Settings):
        self.s = s
        base = (s.paperclip_api_url or "").rstrip("/")
        self.base = base[:-4] if base.endswith("/api") else base

    @property
    def enabled(self) -> bool:
        return bool(self.base and self.s.paperclip_api_key)

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        headers = {"Authorization": f"Bearer {self.s.paperclip_api_key}"}
        if self.s.paperclip_run_id and method != "GET":
            headers["X-Paperclip-Run-Id"] = self.s.paperclip_run_id
        r = requests.request(method, f"{self.base}/api{path}", headers=headers, timeout=30, **kw)
        if r.status_code == 409:
            raise Conflict(r.text)
        if r.status_code >= 400:
            raise requests.HTTPError(f"{r.status_code} {method} {path}: {r.text[:500]}", response=r)
        return r.json() if r.content else {}

    # identity + budget
    def me(self) -> dict[str, Any]:
        return self._req("GET", "/agents/me")

    def agents(self) -> list[dict[str, Any]]:
        res = self._req("GET", f"/companies/{self.s.paperclip_company_id}/agents")
        return res.get("agents", res) if isinstance(res, dict) else res

    def agent_id(self, slug: str) -> str | None:
        for a in self.agents():
            keys = {str(a.get(k, "")).lower() for k in ("urlKey", "slug", "name")}
            if slug.lower() in keys or slug.replace("-", " ").lower() in keys:
                return a["id"]
        return None

    # work
    def assignments(self) -> list[dict[str, Any]]:
        res = self._req("GET", f"/companies/{self.s.paperclip_company_id}/issues",
                        params={"assigneeAgentId": self.s.paperclip_agent_id,
                                "status": "todo,in_progress,in_review,blocked"})
        return res.get("issues", res) if isinstance(res, dict) else res

    def checkout(self, issue_id: str) -> dict[str, Any]:
        return self._req("POST", f"/issues/{issue_id}/checkout",
                         json={"agentId": self.s.paperclip_agent_id,
                               "expectedStatuses": ["todo", "backlog", "blocked", "in_review"]})

    def issue(self, issue_id: str) -> dict[str, Any]:
        return self._req("GET", f"/issues/{issue_id}")

    def update(self, issue_id: str, status: str | None = None, comment: str | None = None,
               unblock_action: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if status:
            body["status"] = status
        if status == "blocked":
            # Paperclip only accepts `blocked` with a blocker, a pending interaction or an unblock descriptor,
            # and agents may only name themselves as the unblock owner.
            body["unblockDescriptor"] = {"owner": {"agentId": self.s.paperclip_agent_id},
                                         "action": (unblock_action or "Review the agent's last comment and decide")[:280]}
        if comment:
            body["comment"] = comment
        return self._req("PATCH", f"/issues/{issue_id}", json=body)

    def comment(self, issue_id: str, body: str) -> dict[str, Any]:
        return self._req("POST", f"/issues/{issue_id}/comments", json={"body": body})

    def create_issue(self, title: str, description: str, assignee_slug: str, parent_id: str | None,
                     goal_id: str | None, priority: str = "high", status: str = "todo") -> dict[str, Any]:
        body: dict[str, Any] = {"title": title, "description": description, "status": status, "priority": priority,
                                "assigneeAgentId": self.agent_id(assignee_slug)}
        if parent_id:
            body["parentId"] = parent_id
        if goal_id:
            body["goalId"] = goal_id
        return self._req("POST", f"/companies/{self.s.paperclip_company_id}/issues", json=body)

    def request_confirmation(self, issue_id: str, prompt: str, key: str,
                             accept: str = "Approve", reject: str = "Request changes") -> dict[str, Any]:
        return self._req("POST", f"/issues/{issue_id}/interactions", json={
            "kind": "request_confirmation", "idempotencyKey": f"confirmation:{issue_id}:{key}",
            "continuationPolicy": "wake_assignee",
            "payload": {"version": 1, "prompt": prompt, "acceptLabel": accept, "rejectLabel": reject,
                        "rejectRequiresReason": True, "supersedeOnUserComment": True}})

    def interactions(self, issue_id: str) -> list[dict[str, Any]]:
        res = self._req("GET", f"/issues/{issue_id}/interactions")
        return res.get("interactions", res) if isinstance(res, dict) else res

    def confirmation_state(self, issue_id: str, key: str) -> str | None:
        """'accepted' | 'rejected' | 'pending' | None for the confirmation with this key."""
        for it in self.interactions(issue_id):
            if str(it.get("idempotencyKey", "")).endswith(key):
                return str(it.get("status") or it.get("state") or "pending").lower()
        return None

    @staticmethod
    def incident_of(issue: dict[str, Any]) -> str | None:
        m = INCIDENT_MARKER.search(f"{issue.get('title', '')}\n{issue.get('description', '')}")
        return m.group(1) if m else None
