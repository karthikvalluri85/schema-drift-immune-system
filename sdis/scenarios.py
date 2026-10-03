"""Synthetic fixtures for the five drift scenarios in snowflake/scenarios/*.sql.

Numbers mirror what the Snowflake seed produces (7 baseline loads of ~3000 orders,
INR amounts with median ≈ 2400, 2000 customers), so tests exercise the same paths
the live demo does.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from sdis.models import ColumnMeta, ColumnProfile

TABLE = "SDIS_DB.RAW.ORDERS"
NEW_LOAD = "L20261003"
BASE_LOADS = [f"L202609{d}" for d in (26, 27, 28, 29, 30)] + ["L20261001", "L20261002"]

BASE_SCHEMA = [
    ColumnMeta("ORDER_ID", "NUMBER(38,0)", 1, False),
    ColumnMeta("CUST_ID", "NUMBER(38,0)", 2, False),
    ColumnMeta("ORDER_TS", "TIMESTAMP_NTZ", 3, False),
    ColumnMeta("AMOUNT", "NUMBER(10,2)", 4, False),
    ColumnMeta("STATUS", "VARCHAR(1)", 5, False),
    ColumnMeta("CHANNEL", "VARCHAR(20)", 6, True),
    ColumnMeta("_LOAD_ID", "VARCHAR", 7, False),
    ColumnMeta("_LOADED_AT", "TIMESTAMP_LTZ", 8, False),
]


def _profile(col: str, load: str, i: int = 0, **kw) -> ColumnProfile:
    defaults = {
        "ORDER_ID": dict(data_type="NUMBER(38,0)", distinct_count=3000, num_median=1500 + 3000 * i),
        "CUST_ID": dict(data_type="NUMBER(38,0)", distinct_count=1550 + (i % 3) * 5, num_median=1000 + (i % 2) * 4),
        "AMOUNT": dict(data_type="NUMBER(10,2)", distinct_count=2990, num_median=2400 + (i % 3) * 6 - 6),
        "STATUS": dict(data_type="VARCHAR(1)", distinct_count=2, top_k={"C": 0.80 + (i % 2) * 0.01, "A": 0.20 - (i % 2) * 0.01}),
        "CHANNEL": dict(data_type="VARCHAR(20)", distinct_count=3, top_k={"web": 0.34, "app": 0.33, "store": 0.33}),
        "ORDER_TS": dict(data_type="TIMESTAMP_NTZ", distinct_count=2990),
    }[col]
    return ColumnProfile(column=col, load_id=load, row_count=3000, null_rate=0.0, **{**defaults, **kw})


def baseline_profiles() -> dict[str, list[ColumnProfile]]:
    cols = ["ORDER_ID", "CUST_ID", "AMOUNT", "STATUS", "CHANNEL", "ORDER_TS"]
    return {c: [_profile(c, load, i) for i, load in enumerate(BASE_LOADS)] for c in cols}


@dataclass
class Scenario:
    name: str
    after: list[ColumnMeta]
    new_profiles: dict[str, ColumnProfile]
    containment: dict[tuple[str, str], float] = field(default_factory=dict)
    restatement: dict | None = None
    expected_class: str = ""
    expected_route: str = ""


def _new(overrides: dict[str, ColumnProfile] | None = None, drop: tuple[str, ...] = ()) -> dict[str, ColumnProfile]:
    base = {c: _profile(c, NEW_LOAD, 7) for c in ["ORDER_ID", "CUST_ID", "AMOUNT", "STATUS", "CHANNEL", "ORDER_TS"]}
    for d in drop:
        base.pop(d, None)
    base.update(overrides or {})
    return base


def _schema(*, drop: tuple[str, ...] = (), add: tuple[ColumnMeta, ...] = (), change: dict[str, str] | None = None):
    out = []
    for c in BASE_SCHEMA:
        if c.name in drop:
            continue
        if change and c.name in change:
            c = replace(c, data_type=change[c.name])
        out.append(c)
    return out + list(add)


def additive() -> Scenario:
    dc = ColumnMeta("DISCOUNT_CODE", "VARCHAR(20)", 9, True)
    return Scenario("additive", _schema(add=(dc,)),
                    _new({"DISCOUNT_CODE": ColumnProfile("DISCOUNT_CODE", NEW_LOAD, "VARCHAR(20)", 3000, 0.7, 3,
                                                         top_k={"DIWALI10": 0.34, "FESTIVE15": 0.33, "NEWUSER": 0.33})}),
                    expected_class="additive", expected_route="auto_document")


def rename() -> Scenario:
    cid = ColumnMeta("CUSTOMER_ID", "NUMBER(38,0)", 2, False)
    new_p = _profile("CUST_ID", NEW_LOAD, 7)
    new_p.column = "CUSTOMER_ID"
    return Scenario("rename", _schema(drop=("CUST_ID",), add=(cid,)), _new({"CUSTOMER_ID": new_p}, drop=("CUST_ID",)),
                    containment={("CUST_ID", "CUSTOMER_ID"): 0.998},
                    expected_class="rename", expected_route="compat_alias_pr")


def type_widening() -> Scenario:
    return Scenario("type_widening", _schema(change={"AMOUNT": "NUMBER(18,2)"}),
                    _new({"AMOUNT": _profile("AMOUNT", NEW_LOAD, 7, data_type="NUMBER(18,2)", num_max=150000000.0)}),
                    expected_class="type_widening", expected_route="contract_update_pr")


def breaking() -> Scenario:
    ia = ColumnMeta("IS_ACTIVE", "BOOLEAN", 9, True)
    return Scenario("breaking", _schema(drop=("STATUS",), add=(ia,)),
                    _new({"IS_ACTIVE": ColumnProfile("IS_ACTIVE", NEW_LOAD, "BOOLEAN", 3000, 0.0, 2,
                                                     top_k={"false": 0.8, "true": 0.2})}, drop=("STATUS",)),
                    expected_class="breaking", expected_route="quarantine_and_remap")


def semantic() -> Scenario:
    return Scenario("semantic", _schema(),
                    _new({"AMOUNT": _profile("AMOUNT", NEW_LOAD, 7, num_median=28.57, distinct_count=2600)}),
                    restatement={"matched_rows": 300, "ratio_median": 84.01, "ratio_cv": 0.004},
                    expected_class="semantic", expected_route="quarantine_and_confirm")


ALL = [additive, rename, type_widening, breaking, semantic]
