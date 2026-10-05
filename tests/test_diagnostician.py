from __future__ import annotations

from sdis import diagnostician, policy, sentinel
from sdis.models import ColumnMeta, ColumnProfile
from sdis.sftypes import compare

TH = policy.load_playbooks()["thresholds"]


def _p(col, load, **kw):
    return ColumnProfile(column=col, load_id=load, row_count=1000, **kw)


def test_type_lattice():
    assert compare("NUMBER(10,2)", "NUMBER(18,2)") == "widening"
    assert compare("NUMBER(18,2)", "NUMBER(10,2)") == "narrowing"
    assert compare("NUMBER(10,2)", "NUMBER(10,4)") == "narrowing"  # integer digits shrink
    assert compare("VARCHAR(20)", "VARCHAR(200)") == "widening"
    assert compare("VARCHAR(200)", "VARCHAR(20)") == "narrowing"
    assert compare("NUMBER(10,2)", "FLOAT") == "widening_lossy"
    assert compare("TIMESTAMP_NTZ", "TIMESTAMP_TZ") == "incompatible"
    assert compare("VARCHAR(1)", "BOOLEAN") == "incompatible"


def test_narrowing_is_breaking():
    before = [ColumnMeta("AMOUNT", "NUMBER(18,2)")]
    after = [ColumnMeta("AMOUNT", "NUMBER(10,2)")]
    events, _ = diagnostician.classify(sentinel.diff_schemas("T", before, after), "L1", {}, {}, {},
                                       lambda a, b: 0.0, TH)
    assert [(e.drift_class, e.subtype) for e in events] == [("breaking", "type_narrowing")]


def test_low_cardinality_columns_are_never_auto_renamed():
    """Fail safe: a 2-value flag 'matches' any other 2-value flag, so it goes to a human instead."""
    before = [ColumnMeta("IS_VIP", "BOOLEAN")]
    after = [ColumnMeta("IS_PRIORITY", "BOOLEAN")]
    old = {"IS_VIP": _p("IS_VIP", "L0", distinct_count=2)}
    new = {"IS_PRIORITY": _p("IS_PRIORITY", "L1", distinct_count=2)}
    events, rejected = diagnostician.classify(sentinel.diff_schemas("T", before, after), "L1", old, new, {},
                                              lambda a, b: 1.0, TH)
    assert {e.drift_class for e in events} == {"breaking", "additive"}
    assert "too small to fingerprint" in rejected[0]["reason"]


def test_rename_requires_matching_values():
    before = [ColumnMeta("CUST_ID", "NUMBER(38,0)")]
    after = [ColumnMeta("ACCOUNT_ID", "NUMBER(38,0)")]
    old = {"CUST_ID": _p("CUST_ID", "L0", distinct_count=1500)}
    new = {"ACCOUNT_ID": _p("ACCOUNT_ID", "L1", distinct_count=1500)}
    events, _ = diagnostician.classify(sentinel.diff_schemas("T", before, after), "L1", old, new, {},
                                       lambda a, b: 0.05, TH)  # values don't overlap → not a rename
    assert {e.drift_class for e in events} == {"breaking", "additive"}


def test_code_meaning_change_is_semantic():
    col = [ColumnMeta("STATUS", "VARCHAR(1)")]
    base = {"STATUS": [_p("STATUS", f"L{i}", top_k={"C": 0.8, "A": 0.2}) for i in range(5)]}
    new = {"STATUS": _p("STATUS", "L9", top_k={"C": 0.2, "A": 0.8})}  # same codes, meanings swapped
    events, _ = diagnostician.classify(sentinel.diff_schemas("T", col, col), "L9", {}, new, base,
                                       lambda a, b: 0.0, TH)
    assert [(e.drift_class, e.subtype) for e in events] == [("semantic", "code_meaning_change")]


def test_normal_variation_is_not_semantic():
    col = [ColumnMeta("AMOUNT", "NUMBER(10,2)")]
    base = {"AMOUNT": [_p("AMOUNT", f"L{i}", num_median=2400 + i * 5) for i in range(7)]}
    new = {"AMOUNT": _p("AMOUNT", "L9", num_median=2600)}  # a busy festival day, not a unit change
    events, _ = diagnostician.classify(sentinel.diff_schemas("T", col, col), "L9", {}, new, base,
                                       lambda a, b: 0.0, TH)
    assert events == []


def test_system_columns_are_ignored():
    before = [ColumnMeta("A", "NUMBER(38,0)"), ColumnMeta("_LOAD_ID", "VARCHAR")]
    after = [ColumnMeta("A", "NUMBER(38,0)"), ColumnMeta("_LOADED_AT", "TIMESTAMP_LTZ")]
    assert not sentinel.diff_schemas("T", before, after).has_structural_change
