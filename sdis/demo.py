"""Credential-free demo mode: the real agents, with Snowflake, GitHub and Jira replaced by local stand-ins.

    SDIS_DEMO=rename sdis heartbeat sentinel     # under Paperclip (set by scripts/paperclip_setup.py --demo)
    sdis demo land rename                        # land a drift scenario (resets the demo state)
    sdis demo status                             # gate, incidents, PRs, tickets

Every agent decision still comes from the real engine and playbooks; only the edges are simulated:
  * warehouse → fixture profiles from sdis/scenarios.py (the same numbers the Snowflake scripts produce)
  * GitHub    → the Surgeon really patches a copy of dbt/ and renders the diff; the "PR" is recorded locally
  * Jira      → tickets are recorded locally
State is one JSON file guarded by a file lock, because each Paperclip heartbeat is its own process.
Use it to try the company without accounts, and to record demos. Never point it at production.
"""
from __future__ import annotations

import atexit
import fcntl
import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import invariants
from . import scenarios as S
from .models import ColumnMeta, ColumnProfile, RouteDecision

NARRATIVE = {
    "rename": ("The orders service renamed its customer key from CUST_ID to CUSTOMER_ID. Every customer-level model "
               "joins on this key, so the Executive Revenue Dashboard, Finance Month-End Close, Customer 360 and "
               "Marketing LTV would all have broken. SDIS held the new load, proved the rename from the column's "
               "values, and opened a one-line fix that leaves every downstream model unchanged."),
    "semantic": ("Order amounts in the latest load are about 84 times smaller than usual, and 300 re-sent orders show a "
                 "constant ratio of 84, consistent with a switch from INR to USD. Nothing errored, so dashboards would "
                 "have shown a 99% revenue drop. The load is quarantined until the producer confirms the currency."),
    "breaking": ("STATUS was removed and replaced by a boolean IS_ACTIVE flag. SDIS quarantined the load, kept a Time "
                 "Travel copy of the table before the change, and drafted a mapping back to the 'A'/'C' codes for review."),
    "type_widening": "AMOUNT widened from NUMBER(10,2) to NUMBER(18,2). No value is lost; the contract type is updated.",
    "additive": "A new DISCOUNT_CODE column arrived. Nothing reads it yet, so it is documented and the load passes.",
}


def state_path() -> Path:
    return Path(os.environ.get("SDIS_DEMO_STATE", ".sdis-demo/state.json")).resolve()


def _dump(d: dict[str, Any]) -> str:
    out = dict(d)
    out["snapshots"] = {k: [c.__dict__ for c in v] for k, v in d.get("snapshots", {}).items()}
    out["profiles"] = {ld: {c: p.__dict__ for c, p in cols.items()} for ld, cols in d.get("profiles", {}).items()}
    out["prs"] = {str(k): v for k, v in d.get("prs", {}).items()}
    return json.dumps(out, default=str)


def _load(text: str) -> dict[str, Any]:
    d = json.loads(text)
    d["snapshots"] = {k: [ColumnMeta(**c) for c in v] for k, v in d.get("snapshots", {}).items()}
    d["profiles"] = {ld: {c: ColumnProfile(**p) for c, p in cols.items()} for ld, cols in d.get("profiles", {}).items()}
    d["prs"] = {int(k): v for k, v in d.get("prs", {}).items()}
    return d


class _Store:
    """One JSON-backed dict, locked for the life of the process that opened it."""

    _lock_fh = None
    _data: dict[str, Any] | None = None
    _path: Path | None = None
    _hooked = False

    @classmethod
    def data(cls) -> dict[str, Any]:
        if cls._data is not None and cls._path != state_path():
            cls.save()  # SDIS_DEMO_STATE changed (tests, several demos): release the old file first
        if cls._data is None:
            p = cls._path = state_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            cls._lock_fh = open(p.with_suffix(".lock"), "w")  # noqa: SIM115 — held until exit
            fcntl.flock(cls._lock_fh, fcntl.LOCK_EX)
            cls._data = _load(p.read_text()) if p.exists() else {}
            if not cls._hooked:
                atexit.register(cls.save)
                cls._hooked = True
        return cls._data

    @classmethod
    def save(cls) -> None:
        if cls._data is not None and cls._path is not None:
            cls._path.write_text(_dump(cls._data))
        if cls._lock_fh is not None:
            fcntl.flock(cls._lock_fh, fcntl.LOCK_UN)
            cls._lock_fh.close()
            cls._lock_fh = None
        cls._data = None


def land(name: str) -> None:
    """Fresh demo state with the 7 clean baseline loads PASSED and scenario `name` waiting in RAW."""
    if name not in {f.__name__ for f in S.ALL}:
        raise SystemExit(f"unknown scenario {name}; choose from {[f.__name__ for f in S.ALL]}")
    _Store.save()
    p = state_path()
    if p.exists():
        p.unlink()
    shutil.rmtree(p.parent / "worktrees", ignore_errors=True)
    d = _Store.data()
    base = S.baseline_profiles()
    d.update({
        "scenario": name,
        "gate": {ld: "PASSED" for ld in S.BASE_LOADS},
        "snapshots": {ld: list(S.BASE_SCHEMA) for ld in S.BASE_LOADS},
        "profiles": {ld: {c: ps[i] for c, ps in base.items()} for i, ld in enumerate(S.BASE_LOADS)},
        "incidents": {}, "prs": {}, "tickets": [], "clones": [], "landed_at": time.time(),
    })
    _Store.save()


def status() -> dict[str, Any]:
    d = _Store.data()
    return {"scenario": d.get("scenario"), "gate": d.get("gate"),
            "incidents": {k: v.get("STATUS") for k, v in d.get("incidents", {}).items()},
            "prs": {n: {"title": p["title"], "merged": p["merged"]} for n, p in d.get("prs", {}).items()},
            "tickets": d.get("tickets", [])}


def _scenario() -> S.Scenario:
    name = _Store.data().get("scenario") or os.environ.get("SDIS_DEMO", "rename")
    return {f.__name__: f for f in S.ALL}[name]()


class DemoWarehouse:
    def __init__(self, agent: str):
        self.agent = agent
        self.d = _Store.data()
        if "gate" not in self.d:
            raise SystemExit("demo state is empty — run `sdis demo land <scenario>` first")
        self.sc = _scenario()

    @contextmanager
    def for_incident(self, inc):
        yield self

    def drift(self, t):
        return f"SDIS_DB.DRIFT.{t}"

    def query(self, sql, params=None):
        invariants.check_sql_is_safe(sql)
        inc = next(iter(self.d["incidents"].values()), {})
        if "max(_loaded_at)" in sql:
            return [{"TS": "2026-10-02 23:59:00"}]
        if "datediff" in sql:
            t0 = self.d["landed_at"]
            ttm = (inc.get("MITIGATED_TS", t0) - t0) / 60
            return [{"TTM": round(ttm, 2), "TTR": round((time.time() - t0) / 60, 2)}]
        return []

    def current_schema(self, table):
        return list(self.sc.after)

    def new_loads(self, table):
        return [] if S.NEW_LOAD in self.d["gate"] else [{"LOAD_ID": S.NEW_LOAD, "ROW_COUNT": 3000}]

    def save_snapshot(self, table, sid, cols, run_id):
        self.d["snapshots"][sid] = list(cols)

    def snapshot(self, table, sid):
        return self.d["snapshots"].get(sid, [])

    def last_passed_snapshot_id(self, table):
        return max(ld for ld, s in self.d["gate"].items() if s == "PASSED" and ld in S.BASE_LOADS)

    def gate_status(self, table, load):
        return self.d["gate"].get(load)

    def set_gate(self, table, load, status, inc, by, row_count=None, approved=False):
        invariants.check_gate_transition(self.d["gate"].get(load), status, approved)
        self.d["gate"][load] = status

    def passed_loads(self, table, limit=7):
        return sorted((ld for ld, s in self.d["gate"].items() if s == "PASSED"), reverse=True)[:limit]

    def profile_load(self, table, load, cols, run_id):
        self.d["profiles"][load] = dict(self.sc.new_profiles)
        return self.d["profiles"][load]

    def profiles(self, table, loads):
        out: dict = {}
        for ld in sorted(loads):
            for c, p in self.d["profiles"].get(ld, {}).items():
                out.setdefault(c, []).append(p)
        return out

    def containment(self, table, old_col, base, new_col, load):
        return self.sc.containment.get((old_col, new_col), 0.1)

    def restatement_ratio(self, table, key, col, load):
        return self.sc.restatement

    def preserve_pre_drift_clone(self, table, inc, ts):
        name = f"SDIS_DB.DRIFT.{table}_PRE_{inc.replace('-', '_')}"
        self.d["clones"].append(name)
        return name

    def save_incident(self, dec: RouteDecision):
        self.d["incidents"].setdefault(dec.incident_id, {"STATUS": "OPEN"})["DECISION"] = dec.to_dict()

    def update_incident(self, inc, **fields):
        row = self.d["incidents"][inc]
        row.update({k.upper(): v for k, v in fields.items()})
        if fields.get("status") in ("MITIGATED", "RESOLVED"):
            row.setdefault("MITIGATED_TS", time.time())

    def incident(self, inc):
        return self.d["incidents"].get(inc)

    def decision(self, inc):
        return RouteDecision.from_dict(self.d["incidents"][inc]["DECISION"])

    def list_incidents(self, limit=50):
        return [{"INCIDENT_ID": k, **{kk: vv for kk, vv in v.items() if kk != "DECISION"}}
                for k, v in self.d["incidents"].items()]

    def cortex_narrative(self, dec):
        return NARRATIVE.get(dec.top_class)


class DemoGitHub:
    """The Surgeon's patch is real (a copy of dbt/ is edited and re-rendered); the PR is recorded locally."""

    def __init__(self, settings):
        self.s = settings
        self.d = _Store.data()

    def worktree(self, branch):
        wt = state_path().parent / "worktrees" / branch.replace("/", "_")
        shutil.rmtree(wt, ignore_errors=True)
        shutil.copytree(self.s.dbt_dir, wt / "dbt", ignore=shutil.ignore_patterns("logs", "target"))
        if (self.s.dbt_dir / "target" / "manifest.json").exists():
            (wt / "dbt" / "target").mkdir(parents=True, exist_ok=True)
            shutil.copy(self.s.dbt_dir / "target" / "manifest.json", wt / "dbt" / "target" / "manifest.json")
        return wt

    def commit_and_push(self, wt, branch, files, message):
        for f in files:
            invariants.check_no_secrets(Path(f).read_text(), where=f)
        return "demo0000"

    def drop_worktree(self, wt):
        pass  # kept so the patched files can be inspected

    def open_pr(self, branch, title, body, draft, labels):
        n = len(self.d["prs"]) + 1
        pr = {"number": n, "node_id": f"DEMO_{n}", "html_url": f"demo://github/{self.s.github_repo}/pull/{n}",
              "draft": draft, "title": title, "body": body, "labels": labels, "merged": False,
              "head": {"sha": "demo0000", "ref": branch}}
        self.d["prs"][n] = pr
        return pr

    def pr(self, n):
        return self.d["prs"][n]

    def ci_state(self, sha):
        return "success"  # the demo's CI stands in for tests + codegen check + Slim CI

    def enable_auto_merge(self, pr):
        pr["merged"] = True

    def merge(self, n, severity, approved, human):
        invariants.check_merge_allowed(severity, approved, human)
        self.d["prs"][n]["merged"] = True

    def comment(self, n, body):
        pass


class DemoJira:
    def __init__(self, settings):
        self.s = settings
        self.d = _Store.data()

    def find_existing(self, inc):
        return next((t["key"] for t in self.d["tickets"] if t["incident"] == inc), None)

    def create_issue(self, dec, narrative, pr_url, owner):
        key = f"SDIS-{len(self.d['tickets']) + 1}"
        self.d["tickets"].append({"key": key, "incident": dec.incident_id, "severity": dec.severity, "owner": owner})
        return key

    def comment(self, key, text):
        pass

    def browse_url(self, key):
        return f"{self.s.jira_base_url}/browse/{key} (demo)"
