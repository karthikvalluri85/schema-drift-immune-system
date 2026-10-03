"""Snowflake IO for SDIS agents.

Every statement passes the bronze-immutable guard and carries a QUERY_TAG
({app, agent, incident}) so the Auditor can attribute compute cost per incident.
Identifiers are validated before interpolation; values are always bound.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from typing import Any

from . import invariants
from .config import Settings
from .models import ColumnMeta, ColumnProfile, DriftEvent, RouteDecision
from .sftypes import from_information_schema, parse

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def ident(name: str) -> str:
    if not _IDENT.match(name or ""):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name.upper()


def _json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


class Warehouse:
    """Thin wrapper around snowflake-connector-python (key-pair auth)."""

    def __init__(self, settings: Settings, agent: str):
        self.s = settings
        self.agent = agent
        self.incident_id: str | None = None
        self._conn = None

    # ------------------------------------------------------------------ plumbing
    def connect(self):
        if self._conn is None:
            import snowflake.connector
            from cryptography.hazmat.primitives import serialization

            if not (self.s.sf_account and self.s.sf_private_key_path):
                raise RuntimeError("SNOWFLAKE_ACCOUNT and SNOWFLAKE_PRIVATE_KEY_PATH must be set")
            with open(self.s.sf_private_key_path, "rb") as fh:
                pkey = serialization.load_pem_private_key(fh.read(), password=None)
            der = pkey.private_bytes(encoding=serialization.Encoding.DER,
                                     format=serialization.PrivateFormat.PKCS8,
                                     encryption_algorithm=serialization.NoEncryption())
            self._conn = snowflake.connector.connect(
                account=self.s.sf_account, user=self.s.sf_user, private_key=der, role=self.s.sf_role,
                warehouse=self.s.sf_warehouse, database=self.s.sf_database,
                session_parameters={"QUERY_TAG": invariants.query_tag(self.agent)},
            )
        return self._conn

    @contextmanager
    def for_incident(self, incident_id: str | None):
        prev = self.incident_id
        self.incident_id = incident_id
        self.query("alter session set query_tag = %s", (invariants.query_tag(self.agent, incident_id),))
        try:
            yield self
        finally:
            self.incident_id = prev
            self.query("alter session set query_tag = %s", (invariants.query_tag(self.agent, prev),))

    def query(self, sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
        invariants.check_sql_is_safe(sql, self.s.raw_schema)
        cur = self.connect().cursor()
        try:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            cols = [c[0].upper() for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            cur.close()

    def drift(self, table: str) -> str:
        return self.s.drift_fqn(table)

    # ------------------------------------------------------------------ schema
    def current_schema(self, table: str) -> list[ColumnMeta]:
        rows = self.query(
            f"""select column_name, ordinal_position, data_type, numeric_precision, numeric_scale,
                       character_maximum_length, is_nullable
                from {ident(self.s.sf_database)}.information_schema.columns
                where table_schema = %s and table_name = %s
                order by ordinal_position""",
            (self.s.raw_schema.upper(), table.upper()),
        )
        return [ColumnMeta(
            name=r["COLUMN_NAME"],
            data_type=str(from_information_schema(r["DATA_TYPE"], r["NUMERIC_PRECISION"], r["NUMERIC_SCALE"],
                                                  r["CHARACTER_MAXIMUM_LENGTH"])),
            ordinal=int(r["ORDINAL_POSITION"]), nullable=r["IS_NULLABLE"] == "YES") for r in rows]

    def save_snapshot(self, table: str, snapshot_id: str, cols: list[ColumnMeta], run_id: str) -> None:
        fqn = self.s.raw_fqn(table)
        self.query(f"delete from {self.drift('SCHEMA_SNAPSHOTS')} where table_fqn = %s and snapshot_id = %s",
                   (fqn, snapshot_id))
        for c in cols:
            t = parse(c.data_type)
            self.query(
                f"""insert into {self.drift('SCHEMA_SNAPSHOTS')}
                    (snapshot_id, table_fqn, column_name, ordinal_position, data_type, numeric_precision,
                     numeric_scale, char_max_length, is_nullable, run_id)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (snapshot_id, fqn, c.name, c.ordinal, c.data_type, t.precision, t.scale, t.length, c.nullable, run_id))

    def snapshot(self, table: str, snapshot_id: str) -> list[ColumnMeta]:
        rows = self.query(
            f"""select column_name, ordinal_position, data_type, is_nullable from {self.drift('SCHEMA_SNAPSHOTS')}
                where table_fqn = %s and snapshot_id = %s order by ordinal_position""",
            (self.s.raw_fqn(table), snapshot_id))
        return [ColumnMeta(r["COLUMN_NAME"], r["DATA_TYPE"], int(r["ORDINAL_POSITION"]), bool(r["IS_NULLABLE"]))
                for r in rows]

    def last_passed_snapshot_id(self, table: str) -> str | None:
        rows = self.query(
            f"""select s.snapshot_id from {self.drift('SCHEMA_SNAPSHOTS')} s
                join {self.drift('LOAD_GATE')} g on g.table_fqn = s.table_fqn and g.load_id = s.snapshot_id
                where s.table_fqn = %s and g.status = 'PASSED'
                group by 1 order by 1 desc limit 1""", (self.s.raw_fqn(table),))
        if rows:
            return rows[0]["SNAPSHOT_ID"]
        rows = self.query(f"select max(snapshot_id) as sid from {self.drift('SCHEMA_SNAPSHOTS')} where table_fqn = %s",
                          (self.s.raw_fqn(table),))
        return rows[0]["SID"] if rows else None

    # ------------------------------------------------------------------ loads + gate
    def new_loads(self, table: str) -> list[dict[str, Any]]:
        fqn = self.s.raw_fqn(table)
        return self.query(
            f"""select r._load_id as load_id, count(*) as row_count, max(r._loaded_at) as loaded_at
                from {ident(self.s.sf_database)}.{ident(self.s.raw_schema)}.{ident(table)} r
                left join {self.drift('LOAD_GATE')} g on g.table_fqn = %s and g.load_id = r._load_id
                where g.load_id is null
                group by 1 order by 1""", (fqn,))

    def gate_status(self, table: str, load_id: str) -> str | None:
        rows = self.query(f"select status from {self.drift('LOAD_GATE')} where table_fqn = %s and load_id = %s",
                          (self.s.raw_fqn(table), load_id))
        return rows[0]["STATUS"] if rows else None

    def set_gate(self, table: str, load_id: str, status: str, incident_id: str | None, decided_by: str,
                 row_count: int | None = None, approved: bool = False) -> None:
        invariants.check_gate_transition(self.gate_status(table, load_id), status, approved)
        self.query(
            f"""merge into {self.drift('LOAD_GATE')} t
                using (select %s as table_fqn, %s as load_id) s
                on t.table_fqn = s.table_fqn and t.load_id = s.load_id
                when matched then update set status = %s, incident_id = coalesce(%s, t.incident_id),
                     decided_by = %s, decided_at = current_timestamp()
                when not matched then insert (table_fqn, load_id, row_count, status, incident_id, decided_by, decided_at)
                     values (s.table_fqn, s.load_id, %s, %s, %s, %s, current_timestamp())""",
            (self.s.raw_fqn(table), load_id, status, incident_id, decided_by, row_count, status, incident_id, decided_by))

    def passed_loads(self, table: str, limit: int = 7) -> list[str]:
        rows = self.query(
            f"""select load_id from {self.drift('LOAD_GATE')} where table_fqn = %s and status = 'PASSED'
                order by load_id desc limit {int(limit)}""", (self.s.raw_fqn(table),))
        return [r["LOAD_ID"] for r in rows]

    # ------------------------------------------------------------------ profiling
    def profile_load(self, table: str, load_id: str, cols: list[ColumnMeta], run_id: str) -> dict[str, ColumnProfile]:
        parts = ["count(*) as row_count"]
        for c in cols:
            if c.name.upper() in ("_LOAD_ID", "_LOADED_AT"):
                continue
            n, fam = ident(c.name), parse(c.data_type).family
            stats = [f"'null_rate', count_if({n} is null) / nullif(count(*), 0)",
                     f"'distinct', approx_count_distinct({n})"]
            if fam == "numeric":
                stats += [f"'mean', avg({n})", f"'median', median({n})", f"'p05', approx_percentile({n}, 0.05)",
                          f"'p95', approx_percentile({n}, 0.95)", f"'min', min({n})", f"'max', max({n})"]
            parts.append(f"object_construct_keep_null({', '.join(stats)}) as {n}__STATS")
            parts.append(f"minhash(64, {n}) as {n}__MH")
            parts.append(f"hll_export(hll_accumulate({n})) as {n}__HLL")
            if fam in ("text", "boolean"):
                parts.append(f"approx_top_k({n}::varchar, 20) as {n}__TOPK")
        row = self.query(
            f"select {', '.join(parts)} from {ident(self.s.sf_database)}.{ident(self.s.raw_schema)}.{ident(table)} "
            f"where _load_id = %s", (load_id,))[0]
        total = int(row["ROW_COUNT"] or 0)
        out: dict[str, ColumnProfile] = {}
        for c in cols:
            n = c.name.upper()
            if n in ("_LOAD_ID", "_LOADED_AT"):
                continue
            st = _json(row[f"{n}__STATS"]) or {}
            topk = _json(row.get(f"{n}__TOPK"))
            top = None
            if topk:
                nn = sum(int(cnt) for _, cnt in topk) or 1
                top = {str(v).lower() if str(v) in ("true", "false") else str(v): int(cnt) / nn for v, cnt in topk}
            out[n] = ColumnProfile(
                column=n, load_id=load_id, data_type=c.data_type, row_count=total,
                null_rate=float(st.get("null_rate") or 0.0), distinct_count=int(st.get("distinct") or 0),
                num_mean=st.get("mean"), num_median=st.get("median"), num_p05=st.get("p05"), num_p95=st.get("p95"),
                num_min=st.get("min"), num_max=st.get("max"), top_k=top,
                minhash=_json(row[f"{n}__MH"]), hll=_json(row[f"{n}__HLL"]))
        self._save_profiles(table, load_id, out, run_id)
        return out

    def _save_profiles(self, table: str, load_id: str, profiles: dict[str, ColumnProfile], run_id: str) -> None:
        fqn = self.s.raw_fqn(table)
        self.query(f"delete from {self.drift('COLUMN_PROFILES')} where table_fqn = %s and load_id = %s", (fqn, load_id))
        for p in profiles.values():
            self.query(
                f"""insert into {self.drift('COLUMN_PROFILES')}
                    (table_fqn, load_id, column_name, data_type, row_count, null_rate, distinct_count, num_mean,
                     num_median, num_p05, num_p95, num_min, num_max, top_k, minhash, hll, run_id)
                    select %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           parse_json(%s), parse_json(%s), parse_json(%s), %s""",
                (fqn, load_id, p.column, p.data_type, p.row_count, p.null_rate, p.distinct_count, p.num_mean,
                 p.num_median, p.num_p05, p.num_p95, p.num_min, p.num_max,
                 json.dumps(p.top_k) if p.top_k else None, json.dumps(p.minhash) if p.minhash else None,
                 json.dumps(p.hll) if p.hll else None, run_id))

    def profiles(self, table: str, load_ids: list[str]) -> dict[str, list[ColumnProfile]]:
        if not load_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(load_ids))
        rows = self.query(
            f"""select * from {self.drift('COLUMN_PROFILES')}
                where table_fqn = %s and load_id in ({placeholders}) order by load_id""",
            (self.s.raw_fqn(table), *load_ids))
        out: dict[str, list[ColumnProfile]] = {}
        for r in rows:
            out.setdefault(r["COLUMN_NAME"], []).append(ColumnProfile(
                column=r["COLUMN_NAME"], load_id=r["LOAD_ID"], data_type=r["DATA_TYPE"], row_count=r["ROW_COUNT"],
                null_rate=r["NULL_RATE"] or 0.0, distinct_count=r["DISTINCT_COUNT"] or 0, num_mean=r["NUM_MEAN"],
                num_median=r["NUM_MEDIAN"], num_p05=r["NUM_P05"], num_p95=r["NUM_P95"], num_min=r["NUM_MIN"],
                num_max=r["NUM_MAX"], top_k=_json(r["TOP_K"]), minhash=_json(r["MINHASH"]), hll=_json(r["HLL"])))
        return out

    def containment(self, table: str, old_col: str, baseline_loads: list[str], new_col: str, new_load: str) -> float:
        """Share of the new column's distinct values already present in the old column's baseline.

        Jaccard J from MINHASH; cardinalities from HLL; |A∩B| = J(|A|+|B|)/(1+J)."""
        fqn = self.s.raw_fqn(table)
        ph = ", ".join(["%s"] * len(baseline_loads))
        row = self.query(
            f"""with b as (select minhash_combine(minhash) as mh, hll_estimate(hll_combine(hll_import(hll))) as n
                           from {self.drift('COLUMN_PROFILES')}
                           where table_fqn = %s and column_name = %s and load_id in ({ph})),
                     n as (select minhash_combine(minhash) as mh, hll_estimate(hll_combine(hll_import(hll))) as n
                           from {self.drift('COLUMN_PROFILES')}
                           where table_fqn = %s and column_name = %s and load_id = %s)
                select (select approximate_similarity(mh) from (select mh from b union all select mh from n)) as j,
                       (select n from b) as nb, (select n from n) as nn""",
            (fqn, old_col.upper(), *baseline_loads, fqn, new_col.upper(), new_load))[0]
        j, nb, nn = float(row["J"] or 0), float(row["NB"] or 0), float(row["NN"] or 0)
        if not nn:
            return 0.0
        return max(0.0, min(1.0, j * (nb + nn) / ((1 + j) * nn)))

    def restatement_ratio(self, table: str, key: str, col: str, new_load: str) -> dict[str, Any] | None:
        """For rows re-sent with the same key, how does the old value relate to the new one?"""
        t = f"{ident(self.s.sf_database)}.{ident(self.s.raw_schema)}.{ident(table)}"
        k, c = ident(key), ident(col)
        row = self.query(
            f"""with n as (select {k} as k, {c} as v from {t} where _load_id = %s),
                     o as (select {k} as k, {c} as v from {t}
                           where _load_id in (select load_id from {self.drift('LOAD_GATE')}
                                              where table_fqn = %s and status = 'PASSED')
                           qualify row_number() over (partition by {k} order by _loaded_at desc) = 1),
                     r as (select o.v / nullif(n.v, 0) as ratio from n join o on n.k = o.k)
                select count(*) as matched, median(ratio) as med, stddev(ratio) / nullif(avg(ratio), 0) as cv from r""",
            (new_load, self.s.raw_fqn(table)))[0]
        if not row["MATCHED"]:
            return None
        return {"matched_rows": int(row["MATCHED"]), "ratio_median": float(row["MED"] or 0),
                "ratio_cv": float(row["CV"] or 0)}

    def preserve_pre_drift_clone(self, table: str, incident_id: str, before_ts: str) -> str:
        """Zero-copy clone of RAW.<table> as it was before the change (Time Travel) — into DRIFT, never RAW."""
        name = f"{ident(table)}_PRE_{incident_id.replace('-', '_')}"
        self.query(
            f"create table if not exists {self.drift(name)} clone "
            f"{ident(self.s.sf_database)}.{ident(self.s.raw_schema)}.{ident(table)} at(timestamp => %s::timestamp_ltz)",
            (before_ts,))
        return self.drift(name)

    # ------------------------------------------------------------------ incidents
    def save_incident(self, d: RouteDecision) -> None:
        blast = json.dumps(d.blast_radius.to_dict() if d.blast_radius else {})
        dec = json.dumps(d.to_dict(), default=str)
        self.query(
            f"""merge into {self.drift('INCIDENTS')} t using (select %s as incident_id) s on t.incident_id = s.incident_id
                when matched then update set severity = %s, route = %s, blast_radius = parse_json(%s),
                     decision = parse_json(%s)
                when not matched then insert (incident_id, table_fqn, load_id, top_class, severity, route, blast_radius,
                     decision, status)
                     values (s.incident_id, %s, %s, %s, %s, %s, parse_json(%s), parse_json(%s), 'OPEN')""",
            (d.incident_id, d.severity, d.route, blast, dec, d.table, d.load_id, d.top_class, d.severity, d.route,
             blast, dec))
        for e in d.events:
            self._save_event(d.incident_id, e)

    def _save_event(self, incident_id: str, e: DriftEvent) -> None:
        self.query(
            f"""merge into {self.drift('DRIFT_EVENTS')} t using (select %s as event_id) s on t.event_id = s.event_id
                when not matched then insert (event_id, incident_id, table_fqn, load_id, drift_class, drift_subtype,
                     column_before, column_after, type_before, type_after, confidence, evidence)
                values (s.event_id, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, parse_json(%s))""",
            (e.event_id, incident_id, e.table, e.load_id, e.drift_class, e.subtype, e.column_before, e.column_after,
             e.type_before, e.type_after, e.confidence, json.dumps(e.evidence, default=str)))

    def update_incident(self, incident_id: str, **fields: Any) -> None:
        allowed = {"status", "narrative", "jira_key", "pr_url", "paperclip_issue"}
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(k)
            sets.append(f"{k} = %s")
            params.append(v)
        if fields.get("status") == "MITIGATED":
            sets.append("mitigated_at = coalesce(mitigated_at, current_timestamp())")
        if fields.get("status") == "RESOLVED":
            sets.append("mitigated_at = coalesce(mitigated_at, current_timestamp())")
            sets.append("resolved_at = current_timestamp()")
        self.query(f"update {self.drift('INCIDENTS')} set {', '.join(sets)} where incident_id = %s",
                   (*params, incident_id))

    def decision(self, incident_id: str) -> RouteDecision:
        row = self.incident(incident_id)
        if not row:
            raise KeyError(incident_id)
        return RouteDecision.from_dict(_json(row["DECISION"]))

    def incident(self, incident_id: str) -> dict[str, Any] | None:
        rows = self.query(f"select * from {self.drift('INCIDENTS')} where incident_id = %s", (incident_id,))
        return rows[0] if rows else None

    def events_for(self, incident_id: str) -> list[DriftEvent]:
        rows = self.query(f"select * from {self.drift('DRIFT_EVENTS')} where incident_id = %s order by event_id",
                          (incident_id,))
        return [DriftEvent(table=r["TABLE_FQN"], load_id=r["LOAD_ID"], drift_class=r["DRIFT_CLASS"],
                           subtype=r["DRIFT_SUBTYPE"], column_before=r["COLUMN_BEFORE"], column_after=r["COLUMN_AFTER"],
                           type_before=r["TYPE_BEFORE"], type_after=r["TYPE_AFTER"], confidence=r["CONFIDENCE"],
                           evidence=_json(r["EVIDENCE"]) or {}) for r in rows]

    def list_incidents(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.query(f"select * from {self.drift('INCIDENTS')} order by opened_at desc limit {int(limit)}")

    # ------------------------------------------------------------------ Cortex
    def cortex_narrative(self, d: RouteDecision) -> str | None:
        """Plain-English summary for humans. Written AFTER routing; never fed back (llm-never-routes)."""
        facts = json.dumps({
            "incident": d.incident_id, "table": d.table, "load": d.load_id, "class": d.top_class,
            "severity": d.severity, "route": d.route, "gate": d.gate,
            "events": [{k: v for k, v in e.to_dict().items() if k != "evidence"} | {"evidence": e.evidence}
                       for e in d.events],
            "blast_radius": d.blast_radius.to_dict() if d.blast_radius else None,
        }, default=str)
        prompt = (
            "You are writing a short incident note for data engineers and business stakeholders. "
            "Using ONLY the JSON facts below, write 3-4 plain sentences: what changed upstream, why it matters "
            "(name the affected dashboards), and what has already been done automatically. Do not invent facts, "
            "do not change the classification or severity, no bullet points.\n\nFACTS:\n" + facts)
        try:
            rows = self.query("select ai_complete(%s, %s) as txt", (self.s.cortex_model, prompt))
            txt = rows[0]["TXT"] if rows else None
            return str(_json(txt)).strip() if txt else None
        except Exception as exc:  # narrative is optional; never block remediation on it
            return f"(Cortex narrative unavailable: {type(exc).__name__})"
