from __future__ import annotations

import pytest

from sdis import invariants as inv
from sdis.invariants import InvariantViolation
from sdis.models import AgentOutput, decision


@pytest.mark.parametrize("sql", [
    "insert into SDIS_DB.RAW.ORDERS values (1)", "delete from RAW.ORDERS", "update raw.orders set a = 1",
    "alter table RAW.ORDERS drop column STATUS", "truncate table RAW.ORDERS", "merge into RAW.ORDERS t using x s on 1=1",
    "create or replace table RAW.ORDERS as select 1", "drop table if exists SDIS_DB.RAW.ORDERS",
])
def test_bronze_immutable_blocks_writes(sql):
    with pytest.raises(InvariantViolation) as e:
        inv.check_sql_is_safe(sql)
    assert e.value.rule_id == "bronze-immutable"


@pytest.mark.parametrize("sql", [
    "select * from SDIS_DB.RAW.ORDERS",
    "insert into SDIS_DB.DRIFT.COLUMN_PROFILES select * from SDIS_DB.RAW.ORDERS",
    "create table if not exists SDIS_DB.DRIFT.ORDERS_PRE_INC_1 clone SDIS_DB.RAW.ORDERS at(offset => -60)",
])
def test_bronze_immutable_allows_reads_and_drift_writes(sql):
    inv.check_sql_is_safe(sql)


def test_gate_transitions():
    inv.check_gate_transition(None, "PENDING")
    inv.check_gate_transition("PENDING", "PASSED")
    inv.check_gate_transition("PENDING", "QUARANTINED")
    inv.check_gate_transition("PASSED", "QUARANTINED")
    with pytest.raises(InvariantViolation):
        inv.check_gate_transition("QUARANTINED", "PASSED")
    inv.check_gate_transition("QUARANTINED", "PASSED", approved=True)


def test_human_approval_sev1_sev2():
    with pytest.raises(InvariantViolation):
        inv.check_merge_allowed("SEV1", approved=False, human_approval_severities=["SEV1", "SEV2"])
    inv.check_merge_allowed("SEV1", approved=True, human_approval_severities=["SEV1", "SEV2"])
    inv.check_merge_allowed("SEV4", approved=False, human_approval_severities=["SEV1", "SEV2"])


def test_tests_before_merge():
    inv.check_ci_green("success")
    for state in ("pending", "failure"):
        with pytest.raises(InvariantViolation):
            inv.check_ci_green(state)


def test_every_decision_traced():
    with pytest.raises(InvariantViolation):
        inv.check_agent_output(AgentOutput(agent="x", run_id="r", status="success"))
    inv.check_agent_output(AgentOutput(agent="x", run_id="r", status="success", decisions=[decision("a", 1, "b")]))


def test_llm_never_routes():
    with pytest.raises(InvariantViolation):
        inv.check_route_inputs({"events": [], "narrative": "the LLM thinks this is fine"})
    inv.check_route_inputs({"events": [], "blast": None})


def test_deterministic_routing_detects_divergence():
    with pytest.raises(InvariantViolation):
        inv.check_deterministic({"route": "a"}, {"route": "b"})


def test_no_credentials_in_code():
    with pytest.raises(InvariantViolation):
        inv.check_no_secrets("-----BEGIN PRIVATE KEY-----\nabc")
    with pytest.raises(InvariantViolation):
        inv.check_no_secrets("token = ghp_" + "a" * 36)
    inv.check_no_secrets("account: {{ env_var('SNOWFLAKE_ACCOUNT') }}")


def test_budget_aware():
    assert inv.budget_mode(10, 100) == "normal"
    assert inv.budget_mode(85, 100) == "critical_only"
    assert inv.budget_mode(100, 100) == "paused"
    assert inv.budget_mode(5, 0) == "normal"


def test_query_tagged():
    assert inv.query_tag("surgeon", "INC-1") == '{"app":"sdis","agent":"surgeon","incident":"INC-1"}'
