"""Snowflake type parsing and the widening lattice.

Widening = every value representable in the old type is representable, unchanged,
in the new type. Anything else is narrowing or incompatible (= breaking).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TYPE_RE = re.compile(r"^\s*([A-Z_ ]+?)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?\s*$")

_ALIASES = {
    "TEXT": "VARCHAR", "STRING": "VARCHAR", "CHAR": "VARCHAR", "CHARACTER": "VARCHAR",
    "DECIMAL": "NUMBER", "NUMERIC": "NUMBER",
    "INT": "INTEGER", "BIGINT": "INTEGER", "SMALLINT": "INTEGER", "TINYINT": "INTEGER", "BYTEINT": "INTEGER",
    "DOUBLE": "FLOAT", "DOUBLE PRECISION": "FLOAT", "REAL": "FLOAT", "FLOAT4": "FLOAT", "FLOAT8": "FLOAT",
    "DATETIME": "TIMESTAMP_NTZ", "TIMESTAMP": "TIMESTAMP_NTZ",
}

FAMILIES = {
    "NUMBER": "numeric", "INTEGER": "numeric", "FLOAT": "numeric",
    "VARCHAR": "text", "BINARY": "binary", "BOOLEAN": "boolean", "DATE": "temporal",
    "TIME": "temporal", "TIMESTAMP_NTZ": "temporal", "TIMESTAMP_LTZ": "temporal", "TIMESTAMP_TZ": "temporal",
    "VARIANT": "semi", "OBJECT": "semi", "ARRAY": "semi",
}

VARCHAR_MAX = 16777216


@dataclass(frozen=True)
class SfType:
    base: str
    precision: int | None = None
    scale: int | None = None
    length: int | None = None

    @property
    def family(self) -> str:
        return FAMILIES.get(self.base, "other")

    def __str__(self) -> str:
        if self.base == "NUMBER":
            return f"NUMBER({self.precision if self.precision is not None else 38},{self.scale or 0})"
        if self.base == "VARCHAR" and self.length and self.length != VARCHAR_MAX:
            return f"VARCHAR({self.length})"
        return self.base


def parse(type_str: str) -> SfType:
    m = _TYPE_RE.match((type_str or "").upper())
    if not m:
        return SfType(base=(type_str or "").upper())
    base, a, b = m.group(1).strip(), m.group(2), m.group(3)
    base = _ALIASES.get(base, base)
    if base == "INTEGER":
        return SfType("NUMBER", 38, 0)
    if base == "NUMBER":
        return SfType("NUMBER", int(a) if a else 38, int(b) if b else 0)
    if base == "VARCHAR":
        return SfType("VARCHAR", length=int(a) if a else VARCHAR_MAX)
    if base.startswith("TIMESTAMP"):
        return SfType(base)
    return SfType(base)


def from_information_schema(data_type: str, precision: int | None, scale: int | None,
                            char_len: int | None) -> SfType:
    base = _ALIASES.get((data_type or "").upper(), (data_type or "").upper())
    if base == "NUMBER":
        return SfType("NUMBER", precision if precision is not None else 38, scale or 0)
    if base == "VARCHAR":
        return SfType("VARCHAR", length=char_len or VARCHAR_MAX)
    return SfType(base)


def compare(old: str, new: str) -> str:
    """Return 'same' | 'widening' | 'widening_lossy' | 'narrowing' | 'incompatible'."""
    a, b = parse(old), parse(new)
    if a == b:
        return "same"
    if a.base == "NUMBER" and b.base == "NUMBER":
        int_digits_a = (a.precision or 38) - (a.scale or 0)
        int_digits_b = (b.precision or 38) - (b.scale or 0)
        if (b.scale or 0) >= (a.scale or 0) and int_digits_b >= int_digits_a:
            return "widening"
        return "narrowing"
    if a.base == "NUMBER" and b.base == "FLOAT":
        return "widening_lossy"
    if a.base == "VARCHAR" and b.base == "VARCHAR":
        return "widening" if (b.length or VARCHAR_MAX) >= (a.length or VARCHAR_MAX) else "narrowing"
    if a.base == "DATE" and b.base.startswith("TIMESTAMP"):
        return "widening"
    if a.base.startswith("TIMESTAMP") and b.base.startswith("TIMESTAMP"):
        return "incompatible"  # tz semantics change; treat as breaking, never silently
    if a.family == "numeric" and b.base == "VARCHAR":
        return "widening_lossy"
    return "incompatible"
