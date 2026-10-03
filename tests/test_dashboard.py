"""Headless render of the Streamlit dashboard in demo mode."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "streamlit" / "streamlit_app.py")


def test_dashboard_renders_in_demo_mode(monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception, at.exception
    labels = [m.label for m in at.metric]
    assert {"Incidents", "Open", "Loads quarantined", "Avg time to contain"} <= set(labels)
    assert any("Demo mode" in c.value for c in at.caption)
