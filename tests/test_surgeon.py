"""Surgeon patches: minimal map edits, regenerated SQL, and a project dbt can still parse."""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest
import yaml

from sdis import codegen
from sdis.simulate import decide, patch_preview


def _copy(src, tmp_path):
    dst = tmp_path / "dbt"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("target", "logs"))
    return dst


def _map(dbt_dir):
    return {c["alias"]: c for c in yaml.safe_load((dbt_dir / "sdis_maps" / "stg_orders.yml").read_text())["columns"]}


@pytest.mark.parametrize("name,expect", [
    ("additive", "Documented new column `DISCOUNT_CODE`"),
    ("rename", "`stg_orders.cust_id` now reads `CUSTOMER_ID`"),
    ("type_widening", "contract number(10,2) → number(18,2)"),
    ("breaking", "`stg_orders.status` derived from `IS_ACTIVE`"),
    ("semantic", "arrive in USD"),
])
def test_patch_summary(name, expect, manifest_dbt_dir):
    _, d = decide(name, manifest_dbt_dir)
    p, diff = patch_preview(d, manifest_dbt_dir)
    assert any(expect in s for s in p.summary), p.summary
    assert diff


def test_rename_diff_touches_one_sql_line(manifest_dbt_dir):
    _, d = decide("rename", manifest_dbt_dir)
    _, diff = patch_preview(d, manifest_dbt_dir)
    sql_part = next(b for b in diff.split("diff --git") if b.splitlines() and b.splitlines()[0].endswith("stg_orders.sql"))
    changed = [ln for ln in sql_part.splitlines() if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))]
    assert changed == ["-        cast(CUST_ID as number(38,0)) as cust_id,",
                       "+        cast(CUSTOMER_ID as number(38,0)) as cust_id,"]


def test_breaking_patch_is_a_draft_with_review_notes(manifest_dbt_dir):
    _, d = decide("breaking", manifest_dbt_dir)
    p, _ = patch_preview(d, manifest_dbt_dir)
    assert p.draft
    assert any("Confirm semantics" in n for n in p.needs_review_notes)
    assert any("Time Travel clone" in n for n in p.needs_review_notes)


def test_semantic_waits_for_confirmation(manifest_dbt_dir, tmp_path):
    from sdis import surgeon
    _, d = decide("semantic", manifest_dbt_dir)
    dbt = _copy(manifest_dbt_dir, tmp_path)
    p = surgeon.normalize_after_confirmation(dbt, d, confirmed=False)
    assert p.empty and "Waiting for producer confirmation" in p.needs_review_notes[0]


@pytest.mark.parametrize("name", ["additive", "rename", "type_widening", "breaking", "semantic"])
def test_patched_project_still_parses_and_codegen_is_clean(name, manifest_dbt_dir, tmp_path):
    from sdis import surgeon
    from sdis.simulate import BREAKING_PROFILES
    _, d = decide(name, manifest_dbt_dir)
    dbt = _copy(manifest_dbt_dir, tmp_path)
    kw = {}
    if name == "breaking":
        kw = {"profiles": BREAKING_PROFILES}
    if name == "semantic":
        d.surgeon_action, kw = "normalize_after_confirmation", {"confirmed": True}
    surgeon.build_patch(dbt, d, **kw)
    assert codegen.run(dbt, check=True) == []
    env = {**os.environ, "SNOWFLAKE_ACCOUNT": "dummy", "SNOWFLAKE_PRIVATE_KEY_PATH": "/dev/null"}
    r = subprocess.run(["dbt", "parse", "--profiles-dir", ".", "--no-partial-parse", "--quiet"], cwd=dbt, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    # aliases (the downstream contract) never change
    assert set(_map(dbt)) == set(_map(manifest_dbt_dir))
