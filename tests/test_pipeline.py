"""End-to-end company run (local mode) with in-memory Snowflake, GitHub and Jira fakes.

Sentinel → Diagnostician → Surgeon / Diplomat → Auditor for every drift scenario, exercising the
same agent handlers Paperclip invokes — only the IO edges are faked.
"""
from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest

from sdis import invariants
from sdis import scenarios as S
from sdis.config import Settings
from sdis.models import RouteDecision
from sdis.runner import run_local

TABLE = "ORDERS"


class FakeWarehouse:
    def __init__(self, sc: S.Scenario):
        self.sc = sc
        self.agent = "test"
        self.gate = {f"L{i}": "PASSED" for i in range(len(S.BASE_LOADS))}
        self.gate = {ld: "PASSED" for ld in S.BASE_LOADS}
        self.snapshots = {ld: list(S.BASE_SCHEMA) for ld in S.BASE_LOADS}
        base = S.baseline_profiles()
        self.prof = {ld: {c: ps[i] for c, ps in base.items()} for i, ld in enumerate(S.BASE_LOADS)}
        self.incidents: dict[str, dict] = {}
        self.events: list = []
        self.clones: list[str] = []
        self.sql_log: list[str] = []

    @contextmanager
    def for_incident(self, inc):
        yield self

    def drift(self, t):
        return f"SDIS_DB.DRIFT.{t}"

    def query(self, sql, params=None):
        invariants.check_sql_is_safe(sql)
        self.sql_log.append(sql)
        if "max(_loaded_at)" in sql:
            return [{"TS": "2026-10-02 23:59:00"}]
        if "datediff" in sql:
            return [{"TTM": 0.7, "TTR": 7.0}]
        return []

    # schema + loads
    def current_schema(self, table):
        return list(self.sc.after)

    def new_loads(self, table):
        return [] if S.NEW_LOAD in self.gate else [{"LOAD_ID": S.NEW_LOAD, "ROW_COUNT": 3000}]

    def save_snapshot(self, table, sid, cols, run_id):
        self.snapshots[sid] = list(cols)

    def snapshot(self, table, sid):
        return self.snapshots.get(sid, [])

    def last_passed_snapshot_id(self, table):
        return max(ld for ld, s in self.gate.items() if s == "PASSED" and ld in S.BASE_LOADS)

    def gate_status(self, table, load):
        return self.gate.get(load)

    def set_gate(self, table, load, status, inc, by, row_count=None, approved=False):
        invariants.check_gate_transition(self.gate.get(load), status, approved)
        self.gate[load] = status

    def passed_loads(self, table, limit=7):
        return sorted((ld for ld, s in self.gate.items() if s == "PASSED"), reverse=True)[:limit]

    def profile_load(self, table, load, cols, run_id):
        self.prof[load] = dict(self.sc.new_profiles)
        return self.prof[load]

    def profiles(self, table, loads):
        out: dict = {}
        for ld in sorted(loads):
            for c, p in self.prof.get(ld, {}).items():
                out.setdefault(c, []).append(p)
        return out

    def containment(self, table, old_col, base, new_col, load):
        return self.sc.containment.get((old_col, new_col), 0.1)

    def restatement_ratio(self, table, key, col, load):
        return self.sc.restatement

    def preserve_pre_drift_clone(self, table, inc, ts):
        name = f"SDIS_DB.DRIFT.{table}_PRE_{inc.replace('-', '_')}"
        self.clones.append(name)
        return name

    # incidents
    def save_incident(self, d: RouteDecision):
        self.incidents.setdefault(d.incident_id, {"STATUS": "OPEN"})["DECISION"] = d.to_dict()

    def update_incident(self, inc, **fields):
        self.incidents[inc].update({k.upper(): v for k, v in fields.items()})

    def incident(self, inc):
        return self.incidents.get(inc)

    def decision(self, inc):
        return RouteDecision.from_dict(self.incidents[inc]["DECISION"])

    def cortex_narrative(self, d):
        return f"(test narrative for {d.incident_id})"


class FakeGitHub:
    def __init__(self, repo_dbt: Path, tmp: Path):
        self.repo_dbt, self.tmp = repo_dbt, tmp
        self.prs: dict[int, dict] = {}
        self.merged: list[int] = []
        self.ci = "success"
        self.auto_merge: list[int] = []

    def worktree(self, branch):
        wt = self.tmp / branch.replace("/", "_")
        shutil.copytree(self.repo_dbt, wt / "dbt", ignore=shutil.ignore_patterns("logs"))
        return wt

    def commit_and_push(self, wt, branch, files, message):
        for f in files:
            invariants.check_no_secrets(Path(f).read_text())
        self.last_files = files
        return "abc123"

    def drop_worktree(self, wt):
        pass

    def open_pr(self, branch, title, body, draft, labels):
        n = len(self.prs) + 1
        self.prs[n] = {"number": n, "node_id": f"PR_{n}", "html_url": f"https://github.com/x/y/pull/{n}", "draft": draft, "title": title,
                       "body": body, "labels": labels, "merged": False, "head": {"sha": "abc123"}}
        return self.prs[n]

    def pr(self, n):
        return self.prs[n]

    def ci_state(self, sha):
        return self.ci

    def enable_auto_merge(self, pr):
        self.auto_merge.append(pr["number"])

    def merge(self, n, severity, approved, human):
        invariants.check_merge_allowed(severity, approved, human)
        self.prs[n]["merged"] = True
        self.merged.append(n)


class FakeJira:
    def __init__(self):
        self.created, self.comments = [], []

    def find_existing(self, inc):
        return None

    def create_issue(self, d, narrative, pr_url, owner):
        self.created.append((d.incident_id, d.severity, owner))
        return f"SDIS-{len(self.created)}"

    def comment(self, key, text):
        self.comments.append((key, text))

    def browse_url(self, key):
        return f"https://jira/browse/{key}"


@pytest.fixture
def company(monkeypatch, manifest_dbt_dir, tmp_path):
    def run(name: str, approve: bool | None = True, confirm: bool | None = True, mode: str = "protect",
            channel: str = "cli", ci: str = "success"):
        sc = {f.__name__: f for f in S.ALL}[name]()
        wh, gh, jr = FakeWarehouse(sc), FakeGitHub(manifest_dbt_dir, tmp_path / name), FakeJira()
        gh.ci = ci
        monkeypatch.setattr("sdis.context.Context.wh", property(lambda self: wh), raising=False)
        monkeypatch.setattr("sdis.context.Context.github", property(lambda self: gh), raising=False)
        monkeypatch.setattr("sdis.context.Context.jira", property(lambda self: jr), raising=False)
        monkeypatch.setattr("sdis.context.Context.manifest", lambda self, dbt_dir=None: None)
        monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
        s = Settings()
        s.repo_dir = manifest_dbt_dir.parent
        s.mode = mode
        s.approval_channel = channel
        s.trace_path = tmp_path / "trace.jsonl"
        outs = run_local(s, approve=approve, confirm=confirm)
        run.settings = s
        return outs, wh, gh, jr
    return run


def _agents(outs):
    return [o.agent for o in outs]


def test_rename_full_loop(company):
    outs, wh, gh, jr = company("rename")
    assert _agents(outs) == ["sentinel", "diagnostician", "surgeon", "diplomat", "auditor"]
    inc = next(iter(wh.incidents))
    assert wh.incidents[inc]["STATUS"] == "RESOLVED"
    assert wh.gate[S.NEW_LOAD] == "PASSED"
    assert gh.merged == [1] and not gh.prs[1]["draft"] and "needs-human" in gh.prs[1]["labels"]
    assert jr.created == [(inc, "SEV1", "orders-team")]
    assert all(o.decisions for o in outs)


def test_rename_without_approval_stays_held(company):
    outs, wh, gh, _ = company("rename", approve=False)
    assert gh.merged == [] and wh.gate[S.NEW_LOAD] == "PENDING"
    assert next(o for o in outs if o.agent == "surgeon").status == "blocked"


def test_additive_auto_merges_without_ticket(company):
    outs, wh, gh, jr = company("additive", approve=None)
    assert _agents(outs) == ["sentinel", "diagnostician", "surgeon", "auditor"]
    assert gh.merged == [1] and jr.created == []
    assert wh.gate[S.NEW_LOAD] == "PASSED"


def test_type_widening_fyi_only(company):
    outs, wh, gh, jr = company("type_widening", approve=None)
    assert gh.merged == [1] and jr.created == []
    assert any(a["type"] == "comment" for o in outs if o.agent == "diplomat" for a in o.artifacts)


def test_breaking_quarantines_clones_and_drafts(company):
    outs, wh, gh, jr = company("breaking", approve=False)
    assert wh.gate[S.NEW_LOAD] == "QUARANTINED"
    assert wh.clones and wh.clones[0].startswith("SDIS_DB.DRIFT.ORDERS_PRE_INC_")
    assert gh.prs[1]["draft"] is True and gh.merged == []
    assert jr.created and jr.created[0][1] == "SEV1"


def test_semantic_waits_for_producer_then_normalises(company):
    outs, wh, gh, jr = company("semantic", confirm=True, approve=True)
    assert _agents(outs) == ["sentinel", "diagnostician", "diplomat", "surgeon", "auditor"]
    assert any("Producer confirmed" in c[1] for c in jr.comments)
    assert gh.merged == [1] and wh.gate[S.NEW_LOAD] == "PASSED"


def test_semantic_rejected_by_producer_stays_quarantined(company):
    outs, wh, gh, _ = company("semantic", confirm=False)
    assert gh.prs == {} and wh.gate[S.NEW_LOAD] == "QUARANTINED"


def test_observe_mode_never_gates_or_patches(company):
    outs, wh, gh, jr = company("breaking", mode="observe")
    assert wh.gate[S.NEW_LOAD] == "PASSED" and gh.prs == {}
    assert jr.created, "observe mode still tells humans"
    assert any(d["decision"] == "adoption_mode" for o in outs for d in o.decisions)


def test_agents_never_write_raw(company):
    _, wh, _, _ = company("breaking", approve=False)
    for sql in wh.sql_log:
        invariants.check_sql_is_safe(sql)


# ---------------------------------------------------------------------------- serverless (GitHub Actions)
def test_github_channel_rename_waits_for_human_merge_then_auditor_resolves(company):
    from sdis.context import Task
    from sdis.runner import incident_from_branch, run_task
    outs, wh, gh, jr = company("rename", approve=None, confirm=None, channel="github")
    surgeon = next(o for o in outs if o.agent == "surgeon")
    assert surgeon.status == "waiting" and gh.merged == [] and gh.auto_merge == []
    assert "merging this PR is the human approval" in gh.prs[1]["body"]
    assert wh.gate[S.NEW_LOAD] == "PENDING"
    # a human merges the PR on GitHub → sdis-on-merge.yml → `sdis on-merge --branch sdis/inc-…`
    inc = incident_from_branch("sdis/" + next(iter(wh.incidents)).lower() + "-rename")
    out = run_task(Task(agent="auditor", title="", description="", incident_id=inc, meta={"approved": "true"}),
                   company.settings)
    assert out[0].status == "success" and wh.gate[S.NEW_LOAD] == "PASSED"
    assert wh.incidents[inc]["STATUS"] == "RESOLVED"


def test_github_channel_sev4_enables_auto_merge_instead_of_polling(company):
    outs, wh, gh, _ = company("additive", approve=None, confirm=None, channel="github", ci="pending")
    assert gh.auto_merge == [1] and gh.merged == []
    assert any(d["decision"] == "auto_merge" for o in outs for d in o.decisions)


def test_github_channel_semantic_waits_for_producer_dispatch(company):
    from sdis.context import Task
    from sdis.runner import run_task
    outs, wh, gh, jr = company("semantic", approve=None, confirm=None, channel="github")
    assert next(o for o in outs if o.agent == "diplomat").status == "waiting" and gh.prs == {}
    inc = next(iter(wh.incidents))
    # the producer answers via sdis-producer.yml → `sdis producer INC --confirm`
    out = run_task(Task(agent="diplomat", title="", description="", incident_id=inc), company.settings, confirm=True)
    assert [o.agent for o in out] == ["diplomat", "surgeon"]
    assert gh.prs[1]["title"].startswith("[SEV1]") and gh.merged == []


def test_incident_from_branch():
    from sdis.runner import incident_from_branch
    assert incident_from_branch("refs/heads/sdis/inc-b1a85bd8-rename") == "INC-B1A85BD8"
    assert incident_from_branch("feature/x") is None
