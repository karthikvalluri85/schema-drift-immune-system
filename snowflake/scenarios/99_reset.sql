-- =============================================================================
-- Reset between demo takes.
--   1) Re-run 02_seed_baseline.sql as SDIS_PRODUCER_ROLE (recreates RAW tables)
--   2) Run this file as SDIS_AGENT_ROLE to clear the control plane
--   3) Run `sdis bootstrap` to snapshot + profile the clean baseline again
-- =============================================================================
use role SDIS_AGENT_ROLE;
use warehouse SDIS_WH;
use schema SDIS_DB.DRIFT;

truncate table if exists SCHEMA_SNAPSHOTS;
truncate table if exists LOAD_GATE;
truncate table if exists COLUMN_PROFILES;
truncate table if exists DRIFT_EVENTS;
truncate table if exists INCIDENTS;
