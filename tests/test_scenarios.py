"""The five drift classes route deterministically to the expected playbook."""
from __future__ import annotations

import pytest

from sdis import invariants, scenarios as S
from sdis.simulate import decide

EXPECTED = {
    #  scenario        class            severity route                     gate          human
    "additive":      ("additive",      "SEV4", "auto_document",          "PASS",       False),
    "rename":        ("rename",        "SEV1", "compat_alias_pr",        "HOLD",       True),
    "type_widening": ("type_widening", "SEV3", "contract_update_pr",     "HOLD",       False),
    "breaking":      ("breaking",      "SEV1", "quarantine_and_remap",   "QUARANTINE", True),
    "semantic":      ("semantic",      "SEV1", "quarantine_and_confirm", "QUARANTINE", True),
}


@pytest.mark.parametrize("name", list(EXPECTED))
def test_route(name, manifest_dbt_dir):
    _, d = decide(name, manifest_dbt_dir)
    cls, sev, route, gate, human = EXPECTED[name]
    assert (d.top_class, d.severity, d.route, d.gate, d.requires_human_approval) == (cls, sev, route, gate, human)
    assert d.decisions, "every route carries its reasoning"


@pytest.mark.parametrize("name", list(EXPECTED))
def test_deterministic_routing(name, manifest_dbt_dir):
    _, a = decide(name, manifest_dbt_dir)
    _, b = decide(name, manifest_dbt_dir)
    invariants.check_deterministic(a.to_dict(), b.to_dict())


def test_rename_has_highest_blast_radius(manifest_dbt_dir):
    _, d = decide("rename", manifest_dbt_dir)
    b = d.blast_radius
    assert b.tier == "high"
    assert b.staging_models == ["stg_orders"]
    assert set(b.downstream_models) == {"fct_orders", "dim_customers", "customer_ltv", "fct_revenue_daily",
                                        "mart_finance_kpis"}
    assert {e["name"] for e in b.exposures} == {"exec_revenue_dashboard", "finance_month_close", "customer_360",
                                                "marketing_ltv_segments"}


def test_additive_has_no_blast_radius(manifest_dbt_dir):
    _, d = decide("additive", manifest_dbt_dir)
    assert d.blast_radius.tier == "low" and not d.blast_radius.downstream_models


def test_semantic_names_the_currency(manifest_dbt_dir):
    _, d = decide("semantic", manifest_dbt_dir)
    e = d.events[0]
    assert e.subtype == "unit_change"
    assert "INR→USD" in e.evidence["hypothesis"]["label"]
    assert e.evidence["restatement_check"]["ratio_median"] == pytest.approx(84, rel=0.01)
    assert e.confidence >= 0.95


def test_breaking_explains_why_not_rename(manifest_dbt_dir):
    _, d = decide("breaking", manifest_dbt_dir)
    dropped = next(e for e in d.events if e.subtype == "column_dropped")
    reasons = [r["why_not_rename"] for r in dropped.evidence["possible_replacements"]]
    assert any("type family" in r for r in reasons)


def test_incident_ids_are_stable():
    # changing nothing must never produce a new incident id between runs or machines
    _, d = decide("rename")
    assert d.incident_id == decide("rename")[1].incident_id
    assert d.incident_id.startswith("INC-") and len(d.incident_id) == 12
    assert S.NEW_LOAD in d.load_id
