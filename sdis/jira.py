"""Jira Cloud REST v3 for the Diplomat (tickets + producer notes)."""
from __future__ import annotations

from typing import Any

import requests

from .config import Settings
from .models import RouteDecision
from .report import CLASS_LABEL, headline, primary_event

PRIORITY = {"SEV1": "Highest", "SEV2": "High", "SEV3": "Medium", "SEV4": "Low"}


def _p(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _h(text: str, level: int = 3) -> dict[str, Any]:
    return {"type": "heading", "attrs": {"level": level}, "content": [{"type": "text", "text": text}]}


def _bullets(items: list[str]) -> dict[str, Any]:
    return {"type": "bulletList", "content": [
        {"type": "listItem", "content": [_p(i)]} for i in items]}


def adf_description(d: RouteDecision, narrative: str | None, pr_url: str | None, producer_note: str) -> dict[str, Any]:
    b = d.blast_radius
    content: list[dict[str, Any]] = [
        _p(f"Incident {d.incident_id} · load {d.load_id} · route {d.route} · gate {d.gate} · SLA {d.sla_minutes} min"),
    ]
    if narrative:
        content += [_h("Summary (Snowflake Cortex, informational)"), _p(narrative)]
    content += [_h("Drift events"), _bullets([
        f"{e.drift_class}/{e.subtype}: {e.column_before or '—'} {e.type_before or ''} → "
        f"{e.column_after or '—'} {e.type_after or ''} (confidence {e.confidence:.2f})" for e in d.events])]
    if b:
        content += [_h(f"Blast radius: {b.tier.upper()}"), _bullets(
            [f"Downstream models: {', '.join(b.downstream_models) or '—'}"]
            + [f"Dashboard/app: {x['label']} (tier {x['tier']}, owner {x.get('owner')})" for x in b.exposures])]
    content += [_h("Deterministic decisions"), _bullets([f"{x['decision']} → {x['choice']}: {x['reasoning']}"
                                                         for x in d.decisions])]
    if pr_url:
        content += [_h("Remediation PR"), _p(pr_url)]
    content += [_h("Note for the producer team"), _p(producer_note)]
    return {"type": "doc", "version": 1, "content": content}


def producer_note(d: RouteDecision, owner: str | None) -> str:
    e = primary_event(d)
    who = owner or "upstream team"
    if not e:
        return ""
    if d.top_class == "rename":
        ask = f"we detected {e.column_before} was renamed to {e.column_after}. Please confirm it is intentional and permanent."
    elif d.top_class == "semantic":
        h = (e.evidence.get("hypothesis") or {}).get("label", "a unit change")
        ask = (f"values in {e.column_before} changed by ×{e.evidence.get('ratio_baseline_over_new')} "
               f"(looks like {h}). Please confirm the unit so we can normalise it.")
    elif d.top_class == "breaking":
        ask = (f"{e.column_before} disappeared from the feed. Please confirm the replacement and its meaning, "
               f"and whether history can be re-sent.")
    elif d.top_class == "type_widening":
        ask = f"{e.column_before} changed type {e.type_before} → {e.type_after}. No action needed unless this was unintended."
    else:
        ask = f"new column {e.column_after} noticed; tell us if downstream teams should start using it."
    return (f"Hi {who} — {ask} Downstream data is protected (load gate: {d.gate}). "
            f"Please treat schema changes as contract changes and announce them in advance.")


class Jira:
    def __init__(self, s: Settings):
        self.s = s

    def _auth(self) -> tuple[str, str]:
        if not (self.s.jira_email and self.s.jira_api_token):
            raise RuntimeError("JIRA_EMAIL and JIRA_API_TOKEN must be set")
        return (self.s.jira_email, self.s.jira_api_token)

    def create_issue(self, d: RouteDecision, narrative: str | None, pr_url: str | None, owner: str | None) -> str:
        payload = {"fields": {
            "project": {"key": self.s.jira_project_key},
            "issuetype": {"name": self.s.jira_issue_type},
            "summary": headline(d)[:250],
            "description": adf_description(d, narrative, pr_url, producer_note(d, owner)),
            "labels": ["sdis", f"sdis-{d.top_class}", d.severity.lower(), d.incident_id.lower()],
        }}
        prio = PRIORITY.get(d.severity)
        if prio:
            payload["fields"]["priority"] = {"name": prio}
        r = requests.post(f"{self.s.jira_base_url}/rest/api/3/issue", json=payload, auth=self._auth(), timeout=30)
        if r.status_code == 400 and "priority" in r.text:
            payload["fields"].pop("priority", None)  # some project schemes hide priority
            r = requests.post(f"{self.s.jira_base_url}/rest/api/3/issue", json=payload, auth=self._auth(), timeout=30)
        r.raise_for_status()
        return r.json()["key"]

    def find_existing(self, incident_id: str) -> str | None:
        r = requests.get(f"{self.s.jira_base_url}/rest/api/3/search/jql", auth=self._auth(), timeout=30,
                         params={"jql": f'project = {self.s.jira_project_key} AND labels = "{incident_id.lower()}"',
                                 "fields": "key", "maxResults": 1})
        if r.ok and r.json().get("issues"):
            return r.json()["issues"][0]["key"]
        return None

    def comment(self, key: str, text: str) -> None:
        requests.post(f"{self.s.jira_base_url}/rest/api/3/issue/{key}/comment", auth=self._auth(), timeout=30,
                      json={"body": {"type": "doc", "version": 1, "content": [_p(text)]}}).raise_for_status()

    def browse_url(self, key: str) -> str:
        return f"{self.s.jira_base_url}/browse/{key}"


__all__ = ["Jira", "producer_note", "adf_description", "CLASS_LABEL"]
