-- =============================================================================
-- Scenario 2 — RENAME drift   ★ LinkedIn demo scenario (highest blast radius) ★
-- At "2 a.m." the orders service renames its join key CUST_ID → CUSTOMER_ID.
-- CUST_ID is the key every customer-level model joins on, so this one change
-- breaks staging, 4 marts and 4 dashboards at once.
--
-- Why it is hard: to the catalog it looks like "CUST_ID dropped + CUSTOMER_ID added".
-- SDIS pairs them by fingerprint: same type, MINHASH Jaccard ≈ 1.0, same null rate,
-- same distinct count → rename (confidence ≥ 0.9).
--
-- Expected SDIS outcome (deterministic):
--   class = rename · severity = SEV1 (blast radius = HIGH) · route = compat_alias_pr
--   Gate: load stays PENDING (dbt keeps serving yesterday's numbers, not errors).
--   Surgeon: PR that aliases `customer_id as cust_id` in stg_orders + updates sources.yml.
--   Diplomat: Jira ticket with blast radius + producer note asking to confirm the rename.
--   Approval: Head of Data Reliability (human-in-the-loop) because blast radius is HIGH.
-- =============================================================================
use role SDIS_PRODUCER_ROLE;
use warehouse SDIS_WH;
use schema SDIS_DB.RAW;

alter table ORDERS rename column CUST_ID to CUSTOMER_ID;

insert into ORDERS (ORDER_ID, CUSTOMER_ID, ORDER_TS, AMOUNT, STATUS, CHANNEL, _LOAD_ID, _LOADED_AT)
select
    21000 + row_number() over (order by seq4()),
    uniform(1, 2000, random()),
    dateadd(second, uniform(0, 86399, random()), '2026-10-03'::timestamp_ntz),
    round(greatest(149, normal(2400, 900, random())), 2),
    iff(uniform(0, 9, random()) < 8, 'C', 'A'),
    array_construct('web','app','store')[uniform(0, 2, random())]::varchar,
    'L20261003',
    current_timestamp()
from table(generator(rowcount => 3000));
