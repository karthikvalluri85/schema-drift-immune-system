"""SDIS API — the always-on front door of the immune system (FastAPI).

Why an API at all, when Paperclip already runs the agents?
  * Webhooks: GitHub (PR merged) and Jira (producer replied "/sdis confirm") must reach the company
    the moment they happen. Webhooks need a public HTTPS endpoint — that is this service.
  * A read API for dashboards, chat-ops and other teams' tools (`/incidents`, `/gate`).
  * A safe way to trigger a scan on demand (`POST /scan`) without giving anyone Snowflake creds.

Run locally:   uvicorn sdis.api:app --reload --port 8000     → open http://localhost:8000/docs
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from functools import lru_cache
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings
from .context import Context, Task

app = FastAPI(
    title="Schema Drift Immune System API",
    version=__version__,
    description=(
        "Webhooks and read API for the SDIS autonomous data-reliability company. "
        "Agents run in Paperclip; this service connects them to GitHub, Jira and dashboards."
    ),
)

SCENARIOS = ["additive", "rename", "type_widening", "breaking", "semantic"]
_BRANCH = re.compile(r"^sdis/(inc-[0-9a-f]{8})-", re.I)


# --------------------------------------------------------------------------- dependencies
@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()


def snowflake_configured(s: Settings = Depends(settings)) -> bool:
    return bool(s.sf_account and s.sf_private_key_path)


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Mutating endpoints need `Authorization: Bearer $SDIS_API_TOKEN`."""
    expected = os.environ.get("SDIS_API_TOKEN")
    if not expected:
        raise HTTPException(503, "SDIS_API_TOKEN is not configured on the server")
    if not authorization or not hmac.compare_digest(authorization, f"Bearer {expected}"):
        raise HTTPException(401, "invalid or missing bearer token")


def _ctx(agent: str) -> Context:
    s = settings()
    mode = "paperclip" if (s.paperclip_api_url and s.paperclip_api_key) else "local"
    return Context(settings=s, agent=agent, mode=mode)


def _dispatch(task: Task, background: BackgroundTasks, priority: str = "high") -> str:
    """Paperclip mode → create an issue for the agent. Local mode → run the agent in the background."""
    ctx = _ctx(task.agent)
    if ctx.mode == "paperclip":
        ctx.delegate(task, priority=priority)
        return "paperclip issue created"

    def _run() -> None:
        from .agents import HANDLERS
        from .runner import _finish
        _finish(ctx, HANDLERS[task.agent](ctx, task))

    background.add_task(_run)
    return "running locally in the background"


# --------------------------------------------------------------------------- schemas
class Health(BaseModel):
    status: str = "ok"
    version: str = __version__
    snowflake: bool
    paperclip: bool
    github: bool
    jira: bool


class SimulationResult(BaseModel):
    scenario: str
    incident_id: str
    drift_class: str
    severity: str
    route: str
    gate: str
    requires_human_approval: bool
    blast_radius: dict[str, Any] | None
    story: list[str] = Field(description="What each agent does, in order")
    patch: list[str] = Field(description="The Surgeon's change, in plain English")
    diff: str = Field(description="Unified diff of the dbt change")


class Accepted(BaseModel):
    accepted: bool = True
    detail: str


# --------------------------------------------------------------------------- read endpoints
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home() -> str:
    return ("<h1>🛡️ Schema Drift Immune System</h1><p>API is running. "
            "Open <a href='/docs'>/docs</a> to explore and try every endpoint.</p>")


@app.get("/health", response_model=Health, tags=["ops"])
def health(s: Settings = Depends(settings), sf: bool = Depends(snowflake_configured)) -> Health:
    """Liveness + which integrations are configured. Used by DigitalOcean health checks."""
    return Health(snowflake=sf, paperclip=bool(s.paperclip_api_url), github=bool(s.github_token),
                  jira=bool(s.jira_api_token))


@app.get("/scenarios", tags=["learn"])
def list_scenarios() -> list[str]:
    return SCENARIOS


@app.get("/scenarios/{name}", response_model=SimulationResult, tags=["learn"])
def simulate_scenario(name: str) -> SimulationResult:
    """Offline: run the real deterministic engine on a built-in drift scenario. No credentials needed."""
    if name not in SCENARIOS:
        raise HTTPException(404, f"unknown scenario; choose one of {SCENARIOS}")
    from .simulate import decide, patch_preview, story
    _, d = decide(name)
    p, diff = patch_preview(d)
    return SimulationResult(scenario=name, incident_id=d.incident_id, drift_class=d.top_class, severity=d.severity,
                            route=d.route, gate=d.gate, requires_human_approval=d.requires_human_approval,
                            blast_radius=d.blast_radius.to_dict() if d.blast_radius else None, story=story(d),
                            patch=p.summary + [f"⚠️ {n}" for n in p.needs_review_notes], diff=diff)


@app.get("/incidents", tags=["incidents"])
def incidents(limit: int = 50, sf: bool = Depends(snowflake_configured)) -> list[dict[str, Any]]:
    """Latest incidents from SDIS_DB.DRIFT.INCIDENTS."""
    if not sf:
        raise HTTPException(503, "Snowflake is not configured; try /scenarios/{name} for an offline demo")
    rows = _ctx("api").wh.list_incidents(min(limit, 200))
    return [{k.lower(): _jsonable(v) for k, v in r.items() if k != "DECISION"} for r in rows]


@app.get("/incidents/{incident_id}", tags=["incidents"])
def incident(incident_id: str, sf: bool = Depends(snowflake_configured)) -> dict[str, Any]:
    if not sf:
        raise HTTPException(503, "Snowflake is not configured")
    row = _ctx("api").wh.incident(incident_id)
    if not row:
        raise HTTPException(404, "incident not found")
    return {k.lower(): _jsonable(v) for k, v in row.items()}


@app.get("/gate", tags=["incidents"])
def gate(sf: bool = Depends(snowflake_configured)) -> list[dict[str, Any]]:
    """Every Bronze load and whether dbt is allowed to read it."""
    if not sf:
        raise HTTPException(503, "Snowflake is not configured")
    wh = _ctx("api").wh
    rows = wh.query(f"select * from {wh.drift('LOAD_GATE')} order by table_fqn, load_id desc limit 200")
    return [{k.lower(): _jsonable(v) for k, v in r.items()} for r in rows]


# --------------------------------------------------------------------------- actions
@app.post("/scan", response_model=Accepted, tags=["ops"], dependencies=[Depends(require_token)])
def scan(background: BackgroundTasks) -> Accepted:
    """Ask the Sentinel to look for new loads now (instead of waiting for its 15-minute routine)."""
    if _ctx("sentinel").mode == "paperclip":
        return Accepted(detail=_dispatch(Task(agent="sentinel", title="On-demand schema scan",
                                              description="Triggered via SDIS API"), background))
    from .runner import run_local
    background.add_task(run_local, settings(), False, False)
    return Accepted(detail="local pipeline started in the background (approvals default to 'no')")


# --------------------------------------------------------------------------- webhooks
def _verify_github(body: bytes, signature: str | None) -> None:
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(503, "GITHUB_WEBHOOK_SECRET is not configured")
    digest = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(digest, signature):
        raise HTTPException(401, "bad GitHub signature")


@app.post("/webhooks/github", response_model=Accepted, tags=["webhooks"])
async def github_webhook(request: Request, background: BackgroundTasks, x_github_event: str | None = Header(default=None),
                         x_hub_signature_256: str | None = Header(default=None)) -> Accepted:
    """GitHub → SDIS. A merged `sdis/inc-…` PR wakes the Auditor immediately."""
    body = await request.body()
    _verify_github(body, x_hub_signature_256)
    if x_github_event == "ping":
        return Accepted(detail="pong")
    payload = json.loads(body or b"{}")
    pr = payload.get("pull_request") or {}
    m = _BRANCH.match((pr.get("head") or {}).get("ref", ""))
    if x_github_event != "pull_request" or not m:
        return Accepted(accepted=False, detail="ignored: not an SDIS pull request event")
    if payload.get("action") == "closed" and pr.get("merged"):
        inc = m.group(1).upper()
        how = _dispatch(Task(agent="auditor", title=f"Verify & close {inc}",
                             description=f"PR #{pr.get('number')} merged by {(pr.get('merged_by') or {}).get('login')}",
                             incident_id=inc, meta={"approved": "true"}), background)
        return Accepted(detail=f"Auditor woken for {inc} ({how})")
    return Accepted(accepted=False, detail=f"ignored action {payload.get('action')}")


@app.post("/webhooks/jira", response_model=Accepted, tags=["webhooks"])
async def jira_webhook(request: Request, background: BackgroundTasks, secret: str | None = None) -> Accepted:
    """Jira → SDIS. When the producer comments `/sdis confirm` on an SDIS ticket, the Surgeon applies the fix.

    Configure in Jira: Settings → System → Webhooks → URL `https://<host>/webhooks/jira?secret=<JIRA_WEBHOOK_SECRET>`,
    event "Comment created", JQL `labels = sdis`.
    """
    expected = os.environ.get("JIRA_WEBHOOK_SECRET")
    if not expected or not secret or not hmac.compare_digest(secret, expected):
        raise HTTPException(401, "bad webhook secret")
    payload = await request.json()
    text = json.dumps((payload.get("comment") or {}).get("body", ""))
    labels = ((payload.get("issue") or {}).get("fields") or {}).get("labels", [])
    inc = next((lbl.upper() for lbl in labels if re.match(r"^inc-[0-9a-f]{8}$", lbl, re.I)), None)
    if not inc:
        return Accepted(accepted=False, detail="ignored: not an SDIS ticket")
    if "/sdis confirm" in text:
        how = _dispatch(Task(agent="surgeon", title=f"Normalise {inc} (producer confirmed in Jira)",
                             description="Producer confirmation received via Jira comment.",
                             incident_id=inc, meta={"confirmed": "true"}), background, priority="critical")
        return Accepted(detail=f"Surgeon asked to normalise {inc} ({how})")
    if "/sdis reject" in text:
        return Accepted(detail=f"{inc}: producer rejected; load stays quarantined for manual triage")
    return Accepted(accepted=False, detail="no /sdis command in comment")


def _jsonable(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v if isinstance(v, int | float | str | bool | list | dict | None) else str(v)
