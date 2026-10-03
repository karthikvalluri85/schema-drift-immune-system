"""Finish a Paperclip import of the SDIS company (run after `paperclipai company import ./company`).

Why this exists: today's `paperclipai company import` CLI maps `process` agents to `claude_local`
and imports routines without their schedule triggers (safe-import defaults). This script applies
the rest of `company/.paperclip.yaml` through the Paperclip REST API:

  * process adapter + `sdis heartbeat <agent>` command for the five deterministic agents
  * heartbeat settings (interval, wake on assignment)
  * schedule triggers for the recurring routines (Schema watch every 15 min, weekly reviews)
  * the company monthly budget (sum of agent budgets)

Usage:
    python scripts/paperclip_setup.py                 # configure, leave agents paused
    python scripts/paperclip_setup.py --activate      # also enable heartbeats and resume agents
    PAPERCLIP_URL=https://paperclip.<ip>.sslip.io PAPERCLIP_TOKEN=... python scripts/paperclip_setup.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
SIDE = yaml.safe_load((ROOT / "company" / ".paperclip.yaml").read_text())
COMPANY_NAME = "Schema Drift Immune System"
TASK_TITLES = {"schema-watch": "Schema watch", "weekly-reliability-review": "Weekly reliability review",
               "weekly-cost-ledger": "Weekly cost ledger"}


def api(base: str, token: str | None):
    s = requests.Session()
    if token:
        s.headers["Authorization"] = f"Bearer {token}"

    def call(method: str, path: str, **kw):
        r = s.request(method, f"{base}/api{path}", timeout=30, **kw)
        if r.status_code >= 400:
            raise SystemExit(f"{method} {path} → {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else {}
    return call


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("PAPERCLIP_URL", "http://127.0.0.1:3100"))
    ap.add_argument("--token", default=os.environ.get("PAPERCLIP_TOKEN"))
    ap.add_argument("--repo-dir", default=os.environ.get("SDIS_REPO_DIR", str(ROOT)))
    ap.add_argument("--activate", action="store_true", help="enable heartbeats and resume agents")
    a = ap.parse_args()
    call = api(a.url.rstrip("/"), a.token)

    companies = call("GET", "/companies")
    companies = companies.get("companies", companies) if isinstance(companies, dict) else companies
    company = next((c for c in companies if c["name"].startswith(COMPANY_NAME)), None)
    if not company:
        print(f"Company '{COMPANY_NAME}' not found — import it first:\n  npx paperclipai company import ./company "
              "--target new --yes")
        return 1
    cid = company["id"]
    agents = call("GET", f"/companies/{cid}/agents")
    agents = agents.get("agents", agents) if isinstance(agents, dict) else agents
    by_key = {a_.get("urlKey"): a_ for a_ in agents}

    total_budget = 0
    for slug, cfg in SIDE["agents"].items():
        ag = by_key.get(slug)
        if not ag:
            print(f"  ! agent {slug} missing")
            continue
        total_budget += int(cfg.get("budgetMonthlyCents", 0))
        hb = dict(cfg.get("runtime", {}).get("heartbeat", {}))
        hb["enabled"] = bool(a.activate and hb.get("enabled", True))
        body: dict = {"runtimeConfig": {"heartbeat": hb}, "budgetMonthlyCents": int(cfg.get("budgetMonthlyCents", 0))}
        if cfg["adapter"]["type"] == "process":
            env = {"SDIS_REPO_DIR": a.repo_dir}
            for k in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PRIVATE_KEY_PATH", "SDIS_CORTEX_MODEL",
                      "GITHUB_REPOSITORY", "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_PROJECT_KEY"):
                if os.environ.get(k):
                    env[k] = os.environ[k]
            body |= {"adapterType": "process", "replaceAdapterConfig": True,
                     "adapterConfig": {**cfg["adapter"]["config"], "cwd": a.repo_dir, "env": env}}
        call("PATCH", f"/agents/{ag['id']}", json=body)
        if a.activate and ag.get("status") == "paused":
            call("POST", f"/agents/{ag['id']}/resume")
        print(f"  ✓ {slug:26} adapter={cfg['adapter']['type']:12} heartbeat={'on' if hb['enabled'] else 'off'} "
              f"budget=${cfg.get('budgetMonthlyCents', 0) / 100:.2f}")

    routines = call("GET", f"/companies/{cid}/routines")
    routines = routines.get("routines", routines) if isinstance(routines, dict) else routines
    for key, spec in SIDE.get("routines", {}).items():
        r = next((x for x in routines if x.get("title") == TASK_TITLES.get(key)), None)
        if not r:
            print(f"  ! routine {key} missing")
            continue
        if not r.get("triggers"):
            for t in spec.get("triggers", []):
                call("POST", f"/routines/{r['id']}/triggers",
                     json={"kind": "schedule", "label": t.get("label"), "cronExpression": t["cronExpression"],
                           "timezone": t.get("timezone", "UTC"), "enabled": bool(a.activate)})
        print(f"  ✓ routine {r['title']:28} {[t['cronExpression'] for t in spec.get('triggers', [])]}")

    call("PATCH", f"/companies/{cid}/budgets", json={"budgetMonthlyCents": total_budget})
    print(f"  ✓ company budget ${total_budget / 100:.2f}/month")
    print(f"\nOpen {a.url} → {company['name']} → Org to see the org chart.")
    if not a.activate:
        print("Agents are configured but paused. Re-run with --activate once Snowflake/GitHub/Jira env vars are set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
