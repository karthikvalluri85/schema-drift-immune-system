-- =============================================================================
-- Scenario 4 — BREAKING drift
-- Producer replaces STATUS VARCHAR ('A'/'C') with IS_ACTIVE BOOLEAN.
-- Different type + different value domain → NOT a rename. A column the contract
-- requires has disappeared.
--
-- Expected SDIS outcome (deterministic):
--   class = breaking · severity = SEV1 (blast radius HIGH) · route = quarantine_and_remap
--   Gate: load QUARANTINED. Marts keep serving the last good build.
--   Surgeon: DRAFT PR that keeps the stg_orders contract (status stays 'A'/'C') and
--            *proposes* `case when IS_ACTIVE then 'A' when not IS_ACTIVE then 'C' end`.
--            The mapping is derived deterministically by matching value shares
--            (baseline A=20%/C=80% ↔ new true=20%/false=80%) and flagged for review.
--   Evidence: zero-copy CLONE of RAW.ORDERS AT(before the change) into DRIFT via
--            Time Travel, so the dropped STATUS values can be backfilled.
--   Diplomat: Jira + producer note; a human must approve the mapping.
-- =============================================================================
use role SDIS_PRODUCER_ROLE;
use warehouse SDIS_WH;
use schema SDIS_DB.RAW;

alter table ORDERS drop column STATUS;
alter table ORDERS add column IS_ACTIVE boolean;

insert into ORDERS (ORDER_ID, CUST_ID, ORDER_TS, AMOUNT, CHANNEL, IS_ACTIVE, _LOAD_ID, _LOADED_AT)
select
    21000 + row_number() over (order by seq4()),
    uniform(1, 2000, random()),
    dateadd(second, uniform(0, 86399, random()), '2026-10-03'::timestamp_ntz),
    round(greatest(149, normal(2400, 900, random())), 2),
    array_construct('web','app','store')[uniform(0, 2, random())]::varchar,
    uniform(0, 9, random()) >= 8,
    'L20261003',
    current_timestamp()
from table(generator(rowcount => 3000));
