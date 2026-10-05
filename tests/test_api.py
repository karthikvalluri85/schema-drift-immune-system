from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from sdis import api


@pytest.fixture
def client(monkeypatch):
    for k in ("SNOWFLAKE_ACCOUNT", "PAPERCLIP_API_URL", "PAPERCLIP_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    api.settings.cache_clear()
    return TestClient(api.app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_docs_are_served(client):
    assert client.get("/docs").status_code == 200
    assert "/webhooks/github" in client.get("/openapi.json").json()["paths"]


def test_simulate_rename(client):
    r = client.get("/scenarios/rename").json()
    assert (r["drift_class"], r["severity"], r["gate"]) == ("rename", "SEV1", "HOLD")
    assert "cast(CUSTOMER_ID as number(38,0)) as cust_id" in r["diff"]


def test_unknown_scenario(client):
    assert client.get("/scenarios/nope").status_code == 404


def test_incidents_need_snowflake(client):
    assert client.get("/incidents").status_code == 503


def test_scan_requires_token(client, monkeypatch):
    monkeypatch.setenv("SDIS_API_TOKEN", "t0ken")
    assert client.post("/scan").status_code == 401


def _sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_github_webhook_rejects_bad_signature(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    r = client.post("/webhooks/github", content=b"{}", headers={"X-GitHub-Event": "ping",
                                                                "X-Hub-Signature-256": "sha256=bad"})
    assert r.status_code == 401


def test_github_webhook_ignores_non_sdis_prs(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    body = json.dumps({"action": "closed", "pull_request": {"merged": True, "head": {"ref": "feature/x"}}}).encode()
    r = client.post("/webhooks/github", content=body, headers={"X-GitHub-Event": "pull_request",
                                                               "X-Hub-Signature-256": _sig("s3cret", body)})
    assert r.status_code == 200 and r.json()["accepted"] is False


def test_github_webhook_wakes_auditor(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    seen = []
    monkeypatch.setattr(api, "_dispatch", lambda task, bg, priority="high": seen.append(task) or "queued")
    body = json.dumps({"action": "closed", "pull_request": {
        "number": 7, "merged": True, "head": {"ref": "sdis/inc-b1a85bd8-rename"}, "merged_by": {"login": "karthik"}}}).encode()
    r = client.post("/webhooks/github", content=body, headers={"X-GitHub-Event": "pull_request",
                                                               "X-Hub-Signature-256": _sig("s3cret", body)})
    assert r.json()["accepted"] is True
    assert seen[0].agent == "auditor" and seen[0].incident_id == "INC-B1A85BD8"


def test_jira_confirm_wakes_surgeon(client, monkeypatch):
    monkeypatch.setenv("JIRA_WEBHOOK_SECRET", "j1ra")
    seen = []
    monkeypatch.setattr(api, "_dispatch", lambda task, bg, priority="high": seen.append(task) or "queued")
    payload = {"comment": {"body": "Yes, it's USD now. /sdis confirm"},
               "issue": {"fields": {"labels": ["sdis", "inc-357e038f"]}}}
    r = client.post("/webhooks/jira?secret=j1ra", json=payload)
    assert r.json()["accepted"] is True
    assert seen[0].agent == "surgeon" and seen[0].meta["confirmed"] == "true"
