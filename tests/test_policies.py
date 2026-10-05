"""Policy-as-code consistency (pattern from the AWS ADOP tool-registry validator)."""
from __future__ import annotations

from pathlib import Path

from sdis import invariants
from sdis.models import DRIFT_CLASSES
from sdis.policy import load_invariants, load_playbooks
from sdis.surgeon import BUILDERS

ROOT = Path(__file__).resolve().parent.parent
PB = load_playbooks()
TIERS = ["low", "medium", "high"]
RANK = {"SEV1": 1, "SEV2": 2, "SEV3": 3, "SEV4": 4}


def test_every_class_has_a_playbook_and_severity_row():
    for cls in DRIFT_CLASSES:
        assert cls in PB["playbooks"], cls
        assert set(PB["severity"][cls]) == set(TIERS)
    assert set(PB["class_precedence"]) == set(DRIFT_CLASSES)


def test_severity_never_improves_as_blast_radius_grows():
    for cls, row in PB["severity"].items():
        ranks = [RANK[row[t]] for t in TIERS]
        assert ranks == sorted(ranks, reverse=True), (cls, row)


def test_playbook_actions_are_implemented():
    for cls, pb in PB["playbooks"].items():
        assert pb["gate"] in {"PASS", "HOLD", "QUARANTINE"}
        assert pb["surgeon"] == "none" or pb["surgeon"] in BUILDERS, (cls, pb["surgeon"])
        assert pb["diplomat"] in {"none", "fyi_comment", "jira_and_producer_note", "jira_and_producer_confirmation"}


def test_destructive_classes_never_pass_the_gate():
    for cls in ("breaking", "semantic"):
        assert PB["playbooks"][cls]["gate"] == "QUARANTINE"
        assert PB["playbooks"][cls]["auto_merge_when_green"] is False


def test_every_invariant_is_enforced_and_tested():
    ids = [r["id"] for r in load_invariants()]
    assert len(ids) == len(set(ids))
    tests = "\n".join(p.read_text() for p in (ROOT / "tests").glob("test_*.py"))
    for rule_id in ids:
        assert rule_id in invariants.ENFORCED, f"{rule_id} has no enforcing function"
        fn = invariants.ENFORCED[rule_id]
        assert fn in tests or rule_id.replace("-", "_") in tests, f"{rule_id} ({fn}) has no test"


def test_codegen_is_up_to_date():
    from sdis import codegen
    assert codegen.run(ROOT / "dbt", check=True) == [], "run `sdis codegen`"
