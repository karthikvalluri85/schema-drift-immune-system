"""Offline simulation of the five drift scenarios — no Snowflake, GitHub or Jira needed.

Runs the *same* deterministic engine and Surgeon patch builders as production against
fixtures that mirror snowflake/scenarios/*.sql, and shows exactly what each agent would do.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import engine, policy, surgeon
from . import scenarios as S
from .config import REPO_ROOT
from .jira import producer_note
from .models import RouteDecision
from .report import headline, markdown
from .router import GATE_TO_STATUS

BREAKING_PROFILES = {"baseline_top_k": {"STATUS": {"C": 0.8, "A": 0.2}},
                     "new_top_k": {"IS_ACTIVE": {"false": 0.8, "true": 0.2}}}


def _ensure_manifest(dbt_dir: Path) -> dict[str, Any] | None:
    mf = dbt_dir / "target" / "manifest.json"
    if not mf.exists():
        env = {**os.environ, "SNOWFLAKE_ACCOUNT": os.environ.get("SNOWFLAKE_ACCOUNT", "offline"),
               "SNOWFLAKE_PRIVATE_KEY_PATH": os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "/dev/null")}
        subprocess.run(["dbt", "parse", "--profiles-dir", ".", "--quiet"], cwd=dbt_dir, env=env, capture_output=True)
    return json.loads(mf.read_text()) if mf.exists() else None


def decide(name: str, dbt_dir: Path | None = None) -> tuple[S.Scenario, RouteDecision]:
    dbt_dir = dbt_dir or REPO_ROOT / "dbt"
    sc = {f.__name__: f for f in S.ALL}[name]()
    base = S.baseline_profiles()
    d = engine.analyze(
        table=S.TABLE, load_id=S.NEW_LOAD, before=S.BASE_SCHEMA, after=sc.after,
        old_profiles={c: v[-1] for c, v in base.items()}, new_profiles=sc.new_profiles, baseline=base,
        similarity=lambda o, n: sc.containment.get((o.column, n.column), 0.12),
        playbooks=policy.load_playbooks(), dbt_dir=dbt_dir, manifest=_ensure_manifest(dbt_dir),
        key_columns={"ORDER_ID"}, restatement=(lambda col: sc.restatement) if sc.restatement else None)
    return sc, d


def patch_preview(d: RouteDecision, dbt_dir: Path | None = None) -> tuple[surgeon.Patch, str]:
    src = dbt_dir or REPO_ROOT / "dbt"
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        ign = shutil.ignore_patterns("target", "logs", "dbt_packages")
        shutil.copytree(src, a / "dbt", ignore=ign)
        shutil.copytree(src, b / "dbt", ignore=ign)
        kw: dict[str, Any] = {}
        if d.surgeon_action == "propose_breaking_remap":
            kw = {"profiles": BREAKING_PROFILES}
        if d.top_class == "semantic":
            d = RouteDecision.from_dict(d.to_dict())
            d.surgeon_action = "normalize_after_confirmation"
            kw = {"confirmed": True}
        p = surgeon.build_patch(b / "dbt", d, **kw)
        diff = subprocess.run(["git", "diff", "--no-index", "--no-color", "a/dbt", "b/dbt"], cwd=tmp,
                              capture_output=True, text=True).stdout
    return p, diff


def story(d: RouteDecision) -> list[str]:
    """What each agent does for this route, in order."""
    gate = GATE_TO_STATUS[d.gate]
    steps = [f"🛰️ Sentinel: new load {d.load_id} profiled; drift signals found → load held PENDING, Diagnostician paged",
             f"🩺 Diagnostician: {headline(d)} → route `{d.route}`, gate **{gate}**"]
    if d.top_class == "breaking":
        steps.append("🧊 Diagnostician: Time Travel zero-copy clone of RAW before the change → DRIFT (evidence & backfill)")
    steps.append("✍️ Cortex AI_COMPLETE writes the narrative (after routing; never changes the route)")
    if d.diplomat_action == "jira_and_producer_confirmation":
        steps += ["📣 Diplomat: Jira ticket + asks the producer to confirm the new meaning (Paperclip confirmation)",
                  "🔧 Surgeon (after confirmation): PR normalising the column back to the contract unit"]
    else:
        if d.surgeon_action != "none":
            steps.append(f"🔧 Surgeon: PR via `{d.surgeon_action}`"
                         + (" (DRAFT — mapping proposed for review)" if d.surgeon_action == "propose_breaking_remap" else ""))
        if d.diplomat_action == "fyi_comment":
            steps.append("📣 Diplomat: FYI note to the producer (no ticket)")
        elif d.diplomat_action != "none":
            steps.append("📣 Diplomat: Jira ticket + producer note")
    steps.append("🧑 Human approval in Paperclip before merge" if d.requires_human_approval
                 else ("🤖 Auto-merge when CI is green" if d.auto_merge_when_green else "👀 Reviewer merges"))
    steps.append("🧾 Auditor: releases the gate → dbt build → RESOLVED, MTTR + cost logged")
    return steps


def simulate(name: str, as_json: bool = False) -> int:
    names = [f.__name__ for f in S.ALL] if name == "all" else [name]
    results = []
    for n in names:
        sc, d = decide(n)
        p, diff = patch_preview(d)
        results.append({"scenario": n, "decision": d.to_dict(), "story": story(d), "patch": p.summary,
                        "review_notes": p.needs_review_notes, "diff": diff,
                        "producer_note": producer_note(d, "orders-team")})
        if not as_json:
            print("=" * 100)
            print(markdown(d))
            print("### What the company does\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(story(d), 1)))
            print("\n### Surgeon patch\n" + ("\n".join(f"- {x}" for x in p.summary) or "- (none)"))
            for note in p.needs_review_notes:
                print(f"- ⚠️ {note}")
            if diff:
                print("\n```diff\n" + diff[:4000] + "\n```")
    if as_json:
        print(json.dumps(results, indent=2, default=str))
    return 0
