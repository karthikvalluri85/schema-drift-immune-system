"""Sentinel — detects *that* something changed. It never decides *what* it means.

Pure functions here; Snowflake IO lives in warehouse.py so everything is unit-testable.
"""
from __future__ import annotations

from .models import ColumnMeta, SchemaDiff
from .sftypes import parse

# Columns the platform adds to every Bronze row; never treated as producer drift.
SYSTEM_COLUMNS = {"_LOAD_ID", "_LOADED_AT"}


def diff_schemas(table: str, before: list[ColumnMeta], after: list[ColumnMeta]) -> SchemaDiff:
    b = {c.name.upper(): c for c in before if c.name.upper() not in SYSTEM_COLUMNS}
    a = {c.name.upper(): c for c in after if c.name.upper() not in SYSTEM_COLUMNS}
    diff = SchemaDiff(table=table)
    for name in sorted(a.keys() - b.keys()):
        diff.added.append(a[name])
    for name in sorted(b.keys() - a.keys()):
        diff.dropped.append(b[name])
    for name in sorted(a.keys() & b.keys()):
        if parse(a[name].data_type) != parse(b[name].data_type):
            diff.type_changed.append((b[name], a[name]))
        else:
            diff.unchanged.append(a[name])
    return diff
