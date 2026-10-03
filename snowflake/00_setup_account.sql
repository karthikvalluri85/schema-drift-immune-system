-- =============================================================================
-- Schema Drift Immune System (SDIS) — one-time account setup
-- Run as ACCOUNTADMIN in a Snowflake worksheet (trial accounts are fine).
--
-- Design principles (see AGENTS.md → invariants):
--   * Least privilege: agents can READ Bronze (RAW) but never write to it.
--   * The "producer" role simulates the upstream app team that changes schemas.
--   * Hard cost guardrails: XS warehouse, 60s auto-suspend, resource monitor.
-- =============================================================================

use role accountadmin;

-- ---------------------------------------------------------------------------
-- 1. Roles
-- ---------------------------------------------------------------------------
create role if not exists SDIS_AGENT_ROLE     comment = 'SDIS agents: read RAW, own DRIFT control plane, call Cortex';
create role if not exists SDIS_TRANSFORM_ROLE comment = 'dbt: read RAW + DRIFT.LOAD_GATE, build ANALYTICS';
create role if not exists SDIS_PRODUCER_ROLE  comment = 'Simulated upstream producer: owns RAW tables, changes schemas';

grant role SDIS_AGENT_ROLE     to role sysadmin;
grant role SDIS_TRANSFORM_ROLE to role sysadmin;
grant role SDIS_PRODUCER_ROLE  to role sysadmin;

-- ---------------------------------------------------------------------------
-- 2. Compute with hard cost guardrails
-- ---------------------------------------------------------------------------
create warehouse if not exists SDIS_WH
  warehouse_size      = 'XSMALL'
  auto_suspend        = 60
  auto_resume         = true
  initially_suspended = true
  comment             = 'SDIS shared XS warehouse';

create resource monitor if not exists SDIS_RM
  with credit_quota = 10
  frequency = monthly
  start_timestamp = immediately
  triggers
    on 75 percent do notify
    on 90 percent do suspend
    on 100 percent do suspend_immediate;

alter warehouse SDIS_WH set resource_monitor = SDIS_RM;

grant usage on warehouse SDIS_WH to role SDIS_AGENT_ROLE;
grant usage on warehouse SDIS_WH to role SDIS_TRANSFORM_ROLE;
grant usage on warehouse SDIS_WH to role SDIS_PRODUCER_ROLE;

-- ---------------------------------------------------------------------------
-- 3. Database and schemas (medallion + control plane)
-- ---------------------------------------------------------------------------
create database if not exists SDIS_DB comment = 'Schema Drift Immune System demo';
create schema if not exists SDIS_DB.RAW       comment = 'Bronze: immutable, append-only landing zone';
create schema if not exists SDIS_DB.DRIFT     comment = 'SDIS control plane: snapshots, profiles, incidents, load gate';
create schema if not exists SDIS_DB.ANALYTICS comment = 'dbt target (staging + marts)';

grant usage on database SDIS_DB to role SDIS_AGENT_ROLE;
grant usage on database SDIS_DB to role SDIS_TRANSFORM_ROLE;
grant usage on database SDIS_DB to role SDIS_PRODUCER_ROLE;

-- Producer owns RAW (so it can ALTER its own tables, like a real app team)
grant usage, create table on schema SDIS_DB.RAW to role SDIS_PRODUCER_ROLE;

-- Agents: READ-ONLY on RAW (invariant: bronze-immutable)
grant usage on schema SDIS_DB.RAW to role SDIS_AGENT_ROLE;
grant select on all tables    in schema SDIS_DB.RAW to role SDIS_AGENT_ROLE;
grant select on future tables in schema SDIS_DB.RAW to role SDIS_AGENT_ROLE;

-- Agents own the control plane
grant usage, create table, create view, create procedure on schema SDIS_DB.DRIFT to role SDIS_AGENT_ROLE;

-- dbt: read RAW, read the load gate, build ANALYTICS
grant usage on schema SDIS_DB.RAW   to role SDIS_TRANSFORM_ROLE;
grant select on all tables    in schema SDIS_DB.RAW to role SDIS_TRANSFORM_ROLE;
grant select on future tables in schema SDIS_DB.RAW to role SDIS_TRANSFORM_ROLE;
grant usage on schema SDIS_DB.DRIFT to role SDIS_TRANSFORM_ROLE;
grant usage, create table, create view on schema SDIS_DB.ANALYTICS to role SDIS_TRANSFORM_ROLE;
-- CI builds each PR into its own schema (ANALYTICS_CI_<pr>_*), never into prod ANALYTICS
grant create schema on database SDIS_DB to role SDIS_TRANSFORM_ROLE;
grant usage on schema SDIS_DB.ANALYTICS to role SDIS_AGENT_ROLE;
grant select on future tables in schema SDIS_DB.ANALYTICS to role SDIS_AGENT_ROLE;
grant select on future views  in schema SDIS_DB.ANALYTICS to role SDIS_AGENT_ROLE;

-- ---------------------------------------------------------------------------
-- 4. Cortex + cost telemetry for the agents
-- ---------------------------------------------------------------------------
grant database role SNOWFLAKE.CORTEX_USER to role SDIS_AGENT_ROLE;
-- ACCOUNT_USAGE access for the Auditor (credits per incident via QUERY_ATTRIBUTION_HISTORY)
grant imported privileges on database SNOWFLAKE to role SDIS_AGENT_ROLE;

-- If your trial region doesn't host the Cortex model you configure, uncomment:
-- alter account set cortex_enabled_cross_region = 'ANY_REGION';

-- ---------------------------------------------------------------------------
-- 5. Service users (key-pair auth — never passwords in code)
--    Generate keys:  openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out sdis_agent.p8
--                    openssl rsa -in sdis_agent.p8 -pubout -out sdis_agent.pub
--    Paste the public key body (no header/footer) below.
-- ---------------------------------------------------------------------------
create user if not exists SDIS_AGENT_SVC
  type = service
  default_role = SDIS_AGENT_ROLE
  default_warehouse = SDIS_WH
  comment = 'SDIS agents (Sentinel, Diagnostician, Auditor)';
-- alter user SDIS_AGENT_SVC set rsa_public_key = '<PASTE_PUBLIC_KEY>';
grant role SDIS_AGENT_ROLE to user SDIS_AGENT_SVC;

create user if not exists SDIS_DBT_SVC
  type = service
  default_role = SDIS_TRANSFORM_ROLE
  default_warehouse = SDIS_WH
  comment = 'dbt runs (CI and the Surgeon''s verification builds)';
-- alter user SDIS_DBT_SVC set rsa_public_key = '<PASTE_PUBLIC_KEY>';
grant role SDIS_TRANSFORM_ROLE to user SDIS_DBT_SVC;

-- The demo presenter plays the upstream producer from a worksheet:
grant role SDIS_PRODUCER_ROLE to role accountadmin;
