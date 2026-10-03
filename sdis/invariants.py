"""Runtime enforcement of policies/invariants.yaml.

Each `check_*` function names the invariant id it enforces; tests assert every id in
the YAML is enforced here and covered by a test.
"""
from __future__ import annotations

import re
from typing import Any

from .policy import load_invariants

ENFORCED: dict[str, str] = {}


class InvariantViolation(RuntimeError):
    def __init__(self, rule_id: str, detail: str):
        super().__init__(f"[{rule_id}] {detail}")
        self.rule_id = rule_id


def _enforces(rule_id: str):
    def wrap(fn):
        ENFORCED[rule_id] = fn.__name__
        return fn
    return wrap


def severity_of(rule_id: str) -> str:
    for r in load_invariants():
        if r["id"] == rule_id:
            return r["severity"]
    raise KeyError(rule_id)


_WRITE_TARGET = re.compile(
    r"^\s*(?:insert\s+(?:overwrite\s+)?into|update|delete\s+from|merge\s+into|truncate\s+(?:table\s+)?(?:if\s+exists\s+)?"
    r"|alter\s+table\s+(?:if\s+exists\s+)?|drop\s+table\s+(?:if\s+exists\s+)?|copy\s+into"
    r"|create\s+(?:or\s+replace\s+)?(?:transient\s+)?table\s+(?:if\s+not\s+exists\s+)?)\s*([\w$.\"]+)",
    re.IGNORECASE,
)


@_enforces("bronze-immutable")
def check_sql_is_safe(sql: str, raw_schema: str = "RAW") -> None:
    """Agents may read RAW (including CLONE … FROM RAW into DRIFT); writing *to* RAW is blocked."""
    stripped = re.sub(r"--[^\n]*|/\*.*?\*/", "", sql, flags=re.S)
    m = _WRITE_TARGET.match(stripped)
    if m:
        target = m.group(1).replace('"', "").upper().split(".")
        schema = target[-2] if len(target) >= 2 else None
        if schema == raw_schema.upper():
            raise InvariantViolation("bronze-immutable", f"agents must never write to {raw_schema} ({'.'.join(target)})")


_ALLOWED_GATE = {
    ("PENDING", "PASSED"), ("PENDING", "QUARANTINED"), ("PENDING", "PENDING"),
    ("QUARANTINED", "QUARANTINED"),
    ("PASSED", "QUARANTINED"),  # re-quarantine is always the safe direction
}


@_enforces("gate-transitions")
def check_gate_transition(current: str | None, new: str, approved: bool = False) -> None:
    cur = current or "PENDING"
    if (cur, new) in _ALLOWED_GATE:
        return
    if cur == "QUARANTINED" and new == "PASSED" and approved:
        return
    if cur == "PASSED" and new == "PASSED":
        return
    raise InvariantViolation("gate-transitions", f"illegal load-gate transition {cur} → {new} (approved={approved})")


@_enforces("human-approval-sev1-sev2")
def check_merge_allowed(severity: str, approved: bool, human_approval_severities: list[str]) -> None:
    if severity in human_approval_severities and not approved:
        raise InvariantViolation("human-approval-sev1-sev2", f"{severity} remediation needs a recorded approval")


@_enforces("tests-before-merge")
def check_ci_green(ci_state: str) -> None:
    if ci_state != "success":
        raise InvariantViolation("tests-before-merge", f"CI state is '{ci_state}', not 'success'")


@_enforces("every-decision-traced")
def check_agent_output(output: Any) -> None:
    decisions = getattr(output, "decisions", None)
    if not decisions:
        raise InvariantViolation("every-decision-traced", f"{getattr(output, 'agent', '?')} produced no decisions[]")


@_enforces("llm-never-routes")
def check_route_inputs(route_inputs: dict[str, Any]) -> None:
    """The router's input must not contain any model-generated text."""
    forbidden = {"narrative", "llm_output", "cortex_output", "completion"}
    leaked = forbidden & set(route_inputs)
    if leaked:
        raise InvariantViolation("llm-never-routes", f"LLM fields {sorted(leaked)} reached the router")


@_enforces("deterministic-routing")
def check_deterministic(first: dict[str, Any], second: dict[str, Any]) -> None:
    keys = ("incident_id", "top_class", "severity", "route", "gate", "requires_human_approval")
    diffs = {k: (first.get(k), second.get(k)) for k in keys if first.get(k) != second.get(k)}
    if diffs:
        raise InvariantViolation("deterministic-routing", f"non-deterministic route: {diffs}")


_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |ENCRYPTED )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bATATT[A-Za-z0-9_\-=]{20,}\b"),  # Atlassian API token
]


@_enforces("no-credentials-in-code")
def check_no_secrets(text: str, where: str = "") -> None:
    for pat in _SECRET_PATTERNS:
        if pat.search(text or ""):
            raise InvariantViolation("no-credentials-in-code", f"credential-like string found {where}".strip())


@_enforces("budget-aware")
def budget_mode(spent_cents: int, budget_cents: int) -> str:
    """'normal' | 'critical_only' (≥80%) | 'paused' (≥100%)."""
    if not budget_cents:
        return "normal"
    used = spent_cents / budget_cents
    if used >= 1.0:
        return "paused"
    return "critical_only" if used >= 0.8 else "normal"


@_enforces("query-tagged")
def query_tag(agent: str, incident_id: str | None = None) -> str:
    import json
    tag = {"app": "sdis", "agent": agent}
    if incident_id:
        tag["incident"] = incident_id
    return json.dumps(tag, separators=(",", ":"))
