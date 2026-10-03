-- =============================================================================
-- SDIS control plane (owned by SDIS_AGENT_ROLE)
-- Everything an incident needs to be explained, costed and audited lives here.
-- =============================================================================
use role SDIS_AGENT_ROLE;
use warehouse SDIS_WH;
use schema SDIS_DB.DRIFT;

-- Point-in-time copy of INFORMATION_SCHEMA.COLUMNS for each watched table.
create table if not exists SCHEMA_SNAPSHOTS (
    snapshot_id        varchar        not null,
    table_fqn          varchar        not null,
    column_name        varchar        not null,
    ordinal_position   number         not null,
    data_type          varchar        not null,
    numeric_precision  number,
    numeric_scale      number,
    char_max_length    number,
    is_nullable        boolean,
    captured_at        timestamp_ltz  default current_timestamp(),
    run_id             varchar
);

-- One row per landed batch. dbt staging models only read loads with status = 'PASSED'.
-- This is the circuit breaker: we quarantine by *gating*, never by mutating Bronze.
create table if not exists LOAD_GATE (
    table_fqn      varchar        not null,
    load_id        varchar        not null,
    row_count      number,
    status         varchar        not null default 'PENDING',   -- PENDING | PASSED | QUARANTINED
    incident_id    varchar,
    decided_by     varchar,
    decided_at     timestamp_ltz,
    first_seen_at  timestamp_ltz  default current_timestamp(),
    constraint pk_load_gate primary key (table_fqn, load_id)
);

-- Per-load, per-column statistical fingerprint. MINHASH + HLL states power rename detection:
-- containment = |new ∩ baseline| / |new| = J·(|A|+|B|) / ((1+J)·|new|).
create table if not exists COLUMN_PROFILES (
    table_fqn       varchar        not null,
    load_id         varchar        not null,
    column_name     varchar        not null,
    data_type       varchar,
    row_count       number,
    null_rate       float,
    distinct_count  number,
    num_mean        float,
    num_median      float,
    num_p05         float,
    num_p95         float,
    num_min         float,
    num_max         float,
    top_k           variant,     -- APPROX_TOP_K for low-cardinality text columns
    minhash         variant,     -- MINHASH(64, col) state  → value overlap (Jaccard)
    hll             variant,     -- HLL_EXPORT state        → cardinality of unions
    profiled_at     timestamp_ltz default current_timestamp(),
    run_id          varchar
);

create table if not exists DRIFT_EVENTS (
    event_id       varchar        not null,
    incident_id    varchar        not null,
    table_fqn      varchar        not null,
    load_id        varchar,
    drift_class    varchar        not null,   -- additive | rename | type_widening | breaking | semantic
    drift_subtype  varchar,
    column_before  varchar,
    column_after   varchar,
    type_before    varchar,
    type_after     varchar,
    confidence     float,
    evidence       variant,
    detected_at    timestamp_ltz  default current_timestamp(),
    run_id         varchar
);

create table if not exists INCIDENTS (
    incident_id     varchar        not null primary key,
    table_fqn       varchar        not null,
    load_id         varchar,
    top_class       varchar        not null,
    severity        varchar        not null,   -- SEV1..SEV4
    route           varchar        not null,   -- playbook route id
    blast_radius    variant,
    decision        variant,                    -- full RouteDecision (deterministic, replayable)
    status          varchar        not null default 'OPEN',  -- OPEN | MITIGATED | RESOLVED
    narrative       varchar,                    -- Cortex-written explanation (never used for routing)
    jira_key        varchar,
    pr_url          varchar,
    paperclip_issue varchar,
    opened_at       timestamp_ltz  default current_timestamp(),
    mitigated_at    timestamp_ltz,
    resolved_at     timestamp_ltz
);

-- Mean time to mitigate / resolve, per drift class.
create or replace view INCIDENT_MTTR as
select
    top_class,
    severity,
    count(*)                                                        as incidents,
    avg(datediff('second', opened_at, mitigated_at)) / 60.0         as avg_minutes_to_mitigate,
    avg(datediff('second', opened_at, resolved_at))  / 60.0         as avg_minutes_to_resolve
from INCIDENTS
group by 1, 2;

-- Snowflake compute attributed to each incident. Agents tag every query with
-- QUERY_TAG = '{"app":"sdis","incident":"<id>","agent":"<name>"}'.
-- ACCOUNT_USAGE has ingestion latency (up to a few hours); fine for the weekly ledger.
create or replace view INCIDENT_COMPUTE_COST as
select
    try_parse_json(q.query_tag):incident::varchar  as incident_id,
    try_parse_json(q.query_tag):agent::varchar     as agent,
    count(*)                                       as queries,
    sum(a.credits_attributed_compute)              as credits
from SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY q
left join SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY a
       on a.query_id = q.query_id
where try_parse_json(q.query_tag):app::varchar = 'sdis'
  and try_parse_json(q.query_tag):incident is not null
group by 1, 2;

-- dbt reads this view; it only exposes PASSED loads.
create or replace view PASSED_LOADS as
select table_fqn, load_id from LOAD_GATE where status = 'PASSED';

grant usage on schema SDIS_DB.DRIFT to role SDIS_TRANSFORM_ROLE;
grant select on view SDIS_DB.DRIFT.PASSED_LOADS to role SDIS_TRANSFORM_ROLE;
