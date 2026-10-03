"""The Paperclip company package (agentcompanies/v1) is internally consistent."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent / "company"


def _front(p: Path) -> dict:
    text = p.read_text()
    assert text.startswith("---\n"), p
    return yaml.safe_load(text.split("---\n")[1])


def test_company_root():
    fm = _front(ROOT / "COMPANY.md")
    assert fm["schema"] == "agentcompanies/v1" and fm["slug"] and fm["goals"]


def test_agents_skills_and_reporting_lines_resolve():
    agents = {p.parent.name: _front(p) for p in (ROOT / "agents").glob("*/AGENTS.md")}
    skills = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    roots = [s for s, fm in agents.items() if fm.get("reportsTo") is None]
    assert roots == ["head-of-data-reliability"]
    for slug, fm in agents.items():
        assert fm["slug"] == slug
        assert fm.get("reportsTo") in (None, *agents), slug
        assert set(fm.get("skills", [])) <= skills, (slug, fm.get("skills"))


def test_sidecar_matches_agents_and_cli():
    side = yaml.safe_load((ROOT / ".paperclip.yaml").read_text())
    agents = {p.parent.name for p in (ROOT / "agents").glob("*/AGENTS.md")}
    assert set(side["agents"]) == agents
    from sdis.agents import HANDLERS
    for slug, cfg in side["agents"].items():
        assert cfg["budgetMonthlyCents"] > 0
        if cfg["adapter"]["type"] == "process":
            # the process adapter spawns `command` directly (no shell), so args must be separate
            assert cfg["adapter"]["config"]["command"] == "sdis"
            assert cfg["adapter"]["config"]["args"] == ["heartbeat", slug]
            assert slug in HANDLERS


def test_recurring_tasks_have_routines():
    side = yaml.safe_load((ROOT / ".paperclip.yaml").read_text())
    for t in (ROOT / "projects").glob("*/tasks/*/TASK.md"):
        fm = _front(t)
        if fm.get("recurring"):
            assert fm["slug"] in side["routines"], fm["slug"]
