"""Credential-free demo mode: the real agents run end to end on recorded data (used for the README GIF)."""
from __future__ import annotations

import pytest

from sdis import demo
from sdis.config import Settings
from sdis.runner import run_local, task_from_issue


@pytest.fixture
def demo_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SDIS_DEMO_STATE", str(tmp_path / "state.pkl"))
    monkeypatch.setenv("SDIS_DEMO", "rename")
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    s = Settings()
    s.trace_path = tmp_path / "trace.jsonl"
    return s


@pytest.mark.parametrize("name,human,final_gate", [
    ("rename", True, "PASSED"), ("additive", False, "PASSED"), ("type_widening", False, "PASSED"),
    ("breaking", True, "QUARANTINED"),  # draft PR: never merged automatically
])
def test_demo_runs_the_whole_company(demo_env, name, human, final_gate):
    demo.land(name)
    outs = run_local(demo_env, approve=False if name == "breaking" else True, confirm=True)
    st = demo.status()
    assert outs[0].agent == "sentinel" and all(o.decisions for o in outs)
    assert st["gate"]["L20261003"] == final_gate
    assert len(st["prs"]) == 1


def test_demo_state_survives_between_processes(demo_env):
    demo.land("rename")
    run_local(demo_env, approve=False)              # approval refused → held
    assert demo.status()["gate"]["L20261003"] == "PENDING"
    demo._Store.save()                              # what process exit does
    assert demo.status()["incidents"]               # a new "process" still sees the incident


def test_demo_needs_a_landed_scenario(demo_env):
    with pytest.raises(SystemExit):
        demo.DemoWarehouse("sentinel")


def test_heartbeat_reads_marker_from_full_description():
    issue = {"id": "i1", "title": "Remediate INC-B1A85BD8", "description": "…long body…\n\n---\nsdis-incident: INC-B1A85BD8"}
    assert task_from_issue("surgeon", issue).incident_id == "INC-B1A85BD8"
