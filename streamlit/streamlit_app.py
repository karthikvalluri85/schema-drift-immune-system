"""SDIS Command Center — Streamlit dashboard.

Runs in three places with the same file:
  1. Streamlit in Snowflake (zero hosting; reads SDIS_DB.DRIFT with the app owner's role)
  2. Locally / on DigitalOcean with `streamlit run streamlit/streamlit_app.py` (key-pair env vars)
  3. Demo mode (no credentials): incidents come from the offline simulator — same engine, fixture data
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # make `sdis` importable when run from the repo

st.set_page_config(page_title="SDIS Command Center", page_icon="🛡️", layout="wide")

SEV_COLORS = {"SEV1": "#d93025", "SEV2": "#f29900", "SEV3": "#f9ab00", "SEV4": "#1a73e8", "SEV5": "#9aa0a6"}
GATE_COLORS = {"PASSED": "#188038", "PENDING": "#f29900", "QUARANTINED": "#d93025"}
CLASS_ICON = {"additive": "➕", "rename": "🔁", "type_widening": "↔️", "breaking": "💥", "semantic": "🧠"}


# ============================================================================ data access
@st.cache_resource
def _backend() -> tuple[str, object]:
    try:  # 1) Streamlit in Snowflake
        from snowflake.snowpark.context import get_active_session
        return "snowflake-native", get_active_session()
    except Exception:  # noqa: S110 — not running inside Snowflake; try the next backend
        pass
    if os.environ.get("SNOWFLAKE_ACCOUNT") and os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"):
        from sdis.config import Settings
        from sdis.warehouse import Warehouse
        return "snowflake-connector", Warehouse(Settings(), agent="dashboard")
    return "demo", None


def sql(query: str) -> pd.DataFrame:
    kind, be = _backend()
    if kind == "snowflake-native":
        return be.sql(query).to_pandas()
    rows = be.query(query)
    return pd.DataFrame(rows)


DB = os.environ.get("SNOWFLAKE_DATABASE", "SDIS_DB")


@st.cache_data(ttl=30)
def load_incidents() -> pd.DataFrame:
    kind, _ = _backend()
    if kind == "demo":
        return demo_incidents()
    df = sql(f"select * from {DB}.DRIFT.INCIDENTS order by opened_at desc limit 500")
    df.columns = [c.upper() for c in df.columns]
    return df


@st.cache_data(ttl=30)
def load_gate() -> pd.DataFrame:
    kind, _ = _backend()
    if kind == "demo":
        rows = [{"TABLE_FQN": "SDIS_DB.RAW.ORDERS", "LOAD_ID": f"L202609{d}", "ROW_COUNT": 3000, "STATUS": "PASSED",
                 "INCIDENT_ID": None, "DECIDED_BY": "sentinel:bootstrap"} for d in (26, 27, 28, 29, 30)]
        rows += [{"TABLE_FQN": "SDIS_DB.RAW.ORDERS", "LOAD_ID": "L20261001", "ROW_COUNT": 3000, "STATUS": "PASSED",
                  "INCIDENT_ID": None, "DECIDED_BY": "sentinel"},
                 {"TABLE_FQN": "SDIS_DB.RAW.ORDERS", "LOAD_ID": "L20261002", "ROW_COUNT": 3000, "STATUS": "PASSED",
                  "INCIDENT_ID": None, "DECIDED_BY": "sentinel"},
                 {"TABLE_FQN": "SDIS_DB.RAW.ORDERS", "LOAD_ID": "L20261003", "ROW_COUNT": 3000, "STATUS": "PENDING",
                  "INCIDENT_ID": "INC-B1A85BD8", "DECIDED_BY": "diagnostician"}]
        return pd.DataFrame(rows)
    df = sql(f"select * from {DB}.DRIFT.LOAD_GATE order by table_fqn, load_id desc")
    df.columns = [c.upper() for c in df.columns]
    return df


@st.cache_data(ttl=300)
def load_costs() -> pd.DataFrame:
    kind, _ = _backend()
    if kind == "demo":
        return pd.DataFrame([{"INCIDENT_ID": i, "AGENT": a, "QUERIES": q, "CREDITS": c} for i, a, q, c in [
            ("INC-B1A85BD8", "sentinel", 14, 0.0031), ("INC-B1A85BD8", "diagnostician", 22, 0.0058),
            ("INC-B1A85BD8", "auditor", 9, 0.0120), ("INC-357E038F", "diagnostician", 25, 0.0064),
            ("INC-27211164", "diagnostician", 24, 0.0071)]])
    try:
        df = sql(f"select * from {DB}.DRIFT.INCIDENT_COMPUTE_COST")
        df.columns = [c.upper() for c in df.columns]
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data
def demo_incidents() -> pd.DataFrame:
    """Five incidents, one per drift class, produced by the real engine on fixture data."""
    from sdis.simulate import decide
    now = datetime.now(UTC)
    timing = {  # minutes after open: (mitigated, resolved or None)
        "additive": (0.4, 6.0), "type_widening": (0.6, 9.5), "rename": (0.7, 7.0),
        "breaking": (0.8, None), "semantic": (0.9, None)}
    rows = []
    for i, name in enumerate(["additive", "type_widening", "breaking", "semantic", "rename"]):
        _, d = decide(name)
        opened = now - timedelta(hours=4 * (5 - i))
        mit, res = timing[name]
        rows.append({
            "INCIDENT_ID": d.incident_id, "TABLE_FQN": d.table, "LOAD_ID": d.load_id, "TOP_CLASS": d.top_class,
            "SEVERITY": d.severity, "ROUTE": d.route, "BLAST_RADIUS": json.dumps(d.blast_radius.to_dict()),
            "DECISION": json.dumps(d.to_dict(), default=str),
            "STATUS": "RESOLVED" if res else ("MITIGATED" if d.gate != "PASS" else "OPEN"),
            "NARRATIVE": DEMO_NARRATIVE.get(name), "JIRA_KEY": f"SDIS-{10 + i}",
            "PR_URL": f"https://github.com/karthikvalluri85/schema-drift-immune-system/pull/{10 + i}",
            "OPENED_AT": opened, "MITIGATED_AT": opened + timedelta(minutes=mit),
            "RESOLVED_AT": opened + timedelta(minutes=res) if res else None})
    return pd.DataFrame(rows)


DEMO_NARRATIVE = {
    "rename": ("The orders service renamed its customer key from CUST_ID to CUSTOMER_ID. Because every customer-level "
               "model joins on this key, the Executive Revenue Dashboard, Finance Month-End Close, Customer 360 and "
               "Marketing LTV models would all have broken. SDIS held the new load, matched the columns by their values, "
               "and opened a one-line fix that keeps the downstream contract unchanged."),
    "semantic": ("Order amounts in the latest load are about 84 times smaller than usual, and 300 re-sent orders show a "
                 "constant ratio of 84 — consistent with the payments team switching from INR to USD. Nothing errored, "
                 "so dashboards would have silently shown a 99% revenue drop. The load is quarantined and the producer "
                 "has been asked to confirm the currency."),
    "breaking": ("The STATUS column was removed and replaced with a boolean IS_ACTIVE flag. SDIS quarantined the load, "
                 "preserved a Time Travel clone of the table before the change, and drafted a mapping from IS_ACTIVE back "
                 "to the 'A'/'C' codes for a human to confirm."),
}


# ============================================================================ helpers
def _json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v or {}


def _minutes(a, b):
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return None
    return (pd.Timestamp(b) - pd.Timestamp(a)).total_seconds() / 60


def lineage_dot(blast: dict, column_label: str) -> str:
    """column → staging → marts → dashboards, as Graphviz DOT."""
    lines = ['digraph G { rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", '
             'fontname="Helvetica", fontsize=11, color="#5f6368", fillcolor="#f1f3f4"]; edge [color="#9aa0a6"];']
    lines.append(f'"{column_label}" [fillcolor="#fce8e6", color="#d93025"];')
    for s in blast.get("staging_models", []):
        lines.append(f'"{s}" [fillcolor="#fef7e0"]; "{column_label}" -> "{s}";')
    marts = blast.get("downstream_models", [])
    for m in marts:
        src = blast["staging_models"][0] if blast.get("staging_models") else column_label
        lines.append(f'"{src}" -> "{m}";')
    for e in blast.get("exposures", []):
        color = "#d93025" if int(e.get("tier", 9)) == 1 else "#1a73e8"
        lines.append(f'"📊 {e["label"]}" [shape=note, fillcolor="#e8f0fe", color="{color}"];')
        target = marts[-1] if marts else column_label
        lines.append(f'"{target}" -> "📊 {e["label"]}" [style=dashed];')
    lines.append("}")
    return "\n".join(lines)


# ============================================================================ UI
kind, _ = _backend()
st.markdown("## 🛡️ Schema Drift Immune System — Command Center")
badge = {"snowflake-native": "🟢 Streamlit in Snowflake", "snowflake-connector": "🟢 Live Snowflake",
         "demo": "🟡 Demo mode (offline simulator — set SNOWFLAKE_* env vars for live data)"}[kind]
st.caption(f"{badge} · agents run in Paperclip · routing is deterministic (policies/playbooks.yaml)")

inc = load_incidents()
gate = load_gate()

tab_overview, tab_incidents, tab_gate, tab_lab, tab_cost = st.tabs(
    ["📈 Overview", "🚨 Incidents", "🚦 Load gate", "🧪 Scenario lab", "💰 Cost & MTTR"])

with tab_overview:
    open_ = inc[inc["STATUS"] != "RESOLVED"] if not inc.empty else inc
    ttm = [m for m in (_minutes(r.OPENED_AT, r.MITIGATED_AT) for r in inc.itertuples()) if m is not None]
    ttr = [m for m in (_minutes(r.OPENED_AT, r.RESOLVED_AT) for r in inc.itertuples()) if m is not None]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Incidents", len(inc))
    c2.metric("Open", len(open_))
    c3.metric("Loads quarantined", int((gate["STATUS"] == "QUARANTINED").sum()) if not gate.empty else 0)
    c4.metric("Avg time to contain", f"{sum(ttm) / len(ttm):.1f} min" if ttm else "—",
              help="Opened → load gated (bad data can no longer reach marts)")
    c5.metric("Avg time to resolve", f"{sum(ttr) / len(ttr):.1f} min" if ttr else "—",
              help="Opened → fix merged, gate released, dbt build green")
    if not inc.empty:
        left, right = st.columns(2)
        with left:
            st.markdown("**Incidents by drift class**")
            st.bar_chart(inc.groupby("TOP_CLASS").size().rename("incidents"))
        with right:
            st.markdown("**Incidents by severity**")
            st.bar_chart(inc.groupby("SEVERITY").size().rename("incidents"))
        st.markdown("**Latest**")
        for r in inc.head(5).itertuples():
            st.markdown(f"{CLASS_ICON.get(r.TOP_CLASS, '•')} **{r.SEVERITY}** `{r.INCIDENT_ID}` "
                        f"{r.TOP_CLASS} on `{str(r.TABLE_FQN).split('.')[-1]}` load `{r.LOAD_ID}` — **{r.STATUS}**")

with tab_incidents:
    if inc.empty:
        st.info("No incidents yet. Land a drift scenario from snowflake/scenarios/ and run the Sentinel.")
    else:
        view = inc[["INCIDENT_ID", "SEVERITY", "TOP_CLASS", "TABLE_FQN", "LOAD_ID", "ROUTE", "STATUS", "JIRA_KEY",
                    "OPENED_AT"]]
        st.dataframe(view, use_container_width=True, hide_index=True)
        pick = st.selectbox("Incident", inc["INCIDENT_ID"].tolist(),
                            format_func=lambda i: f"{i} · {inc.set_index('INCIDENT_ID').loc[i, 'TOP_CLASS']}")
        row = inc.set_index("INCIDENT_ID").loc[pick]
        dec = _json(row.get("DECISION"))
        blast = _json(row.get("BLAST_RADIUS"))
        st.markdown(f"### {CLASS_ICON.get(row.TOP_CLASS, '')} {row.SEVERITY} · {row.TOP_CLASS} · `{pick}`")
        a, b, c, d_ = st.columns(4)
        a.metric("Gate", dec.get("gate", "—"))
        b.metric("Route", row.ROUTE)
        c.metric("Blast radius", str(blast.get("tier", "low")).upper())
        d_.metric("Human approval", "required" if dec.get("requires_human_approval") else "policy")
        if row.get("NARRATIVE"):
            st.info(f"✍️ **Cortex summary** (informational — never used for routing)\n\n{row.NARRATIVE}")
        links = []
        if row.get("JIRA_KEY"):
            links.append(f"[Jira {row.JIRA_KEY}](https://karthikvalluri1985.atlassian.net/browse/{row.JIRA_KEY})")
        if row.get("PR_URL"):
            links.append(f"[Pull request]({row.PR_URL})")
        if links:
            st.markdown(" · ".join(links))
        ev = pd.DataFrame(dec.get("events", []))
        if not ev.empty:
            st.markdown("**Drift events**")
            st.dataframe(ev[["drift_class", "subtype", "column_before", "type_before", "column_after", "type_after",
                             "confidence"]], use_container_width=True, hide_index=True)
        if blast.get("staging_models"):
            st.markdown("**Blast radius** — what would have broken")
            first = (dec.get("events") or [{}])[0]
            st.graphviz_chart(lineage_dot(blast, f"RAW.{first.get('column_before') or first.get('column_after')}"))
        with st.expander("Deterministic decisions (the audit trail)"):
            for x in dec.get("decisions", []):
                st.markdown(f"- **{x['decision']}** → `{x['choice']}` — {x['reasoning']}")

with tab_gate:
    st.markdown("Every Bronze batch and whether dbt may read it. Quarantine never deletes data — it hides the batch.")
    if not gate.empty:
        def _style(v):
            return f"color: {GATE_COLORS.get(v, '#000')}; font-weight: 600"
        cols = [c for c in ["TABLE_FQN", "LOAD_ID", "ROW_COUNT", "STATUS", "INCIDENT_ID", "DECIDED_BY", "DECIDED_AT"]
                if c in gate.columns]
        st.dataframe(gate[cols].style.map(_style, subset=["STATUS"]), use_container_width=True, hide_index=True)

with tab_lab:
    st.markdown("Run the **real** engine on each of the five drift scenarios (fixture data, no Snowflake needed).")
    try:
        from sdis.simulate import decide, patch_preview, story
        name = st.radio("Scenario", ["additive", "rename", "type_widening", "breaking", "semantic"], horizontal=True,
                        format_func=lambda n: f"{CLASS_ICON[n]} {n}")
        _, d = decide(name)
        p, diff = patch_preview(d)
        x1, x2, x3, x4 = st.columns(4)
        x1.metric("Class", d.top_class)
        x2.metric("Severity", d.severity)
        x3.metric("Gate", d.gate)
        x4.metric("Approval", "human" if d.requires_human_approval else "policy")
        st.markdown("**What the company does**")
        for i, s in enumerate(story(d), 1):
            st.markdown(f"{i}. {s}")
        if d.blast_radius and d.blast_radius.staging_models:
            e0 = d.events[0]
            st.graphviz_chart(lineage_dot(d.blast_radius.to_dict(), f"RAW.{e0.column_before or e0.column_after}"))
        st.markdown("**Surgeon's change**")
        for s in p.summary:
            st.markdown(f"- {s}")
        for n in p.needs_review_notes:
            st.warning(n)
        if diff:
            st.code(diff, language="diff")
    except Exception as exc:  # Streamlit in Snowflake doesn't ship the sdis package
        st.info(f"Scenario lab needs the `sdis` package (run the dashboard from the repo). ({type(exc).__name__})")

with tab_cost:
    st.markdown("Snowflake compute attributed to each incident via `QUERY_TAG` "
                "(`ACCOUNT_USAGE` lags a few hours). LLM token spend per agent lives in Paperclip → Costs.")
    costs = load_costs()
    if costs.empty:
        st.info("No attributed compute yet.")
    else:
        st.dataframe(costs, use_container_width=True, hide_index=True)
        st.bar_chart(costs.groupby("AGENT")["CREDITS"].sum())
        credits = float(costs["CREDITS"].sum())
        st.metric("Total credits", f"{credits:.4f}", help="Trial accounts price a credit at roughly $2–4")
    if not inc.empty:
        mt = inc.assign(TTM=[_minutes(r.OPENED_AT, r.MITIGATED_AT) for r in inc.itertuples()],
                        TTR=[_minutes(r.OPENED_AT, r.RESOLVED_AT) for r in inc.itertuples()])
        st.markdown("**Minutes to contain / resolve by class**")
        st.dataframe(mt.groupby("TOP_CLASS")[["TTM", "TTR"]].mean().round(1), use_container_width=True)
