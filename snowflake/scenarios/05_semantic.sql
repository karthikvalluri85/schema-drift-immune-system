-- =============================================================================
-- Scenario 5 — SEMANTIC drift (the silent one)
-- The payments team switches AMOUNT from INR to USD. Same name, same type,
-- every dbt test still passes — revenue dashboards would show a ~99% drop.
-- The batch also restates 300 orders from L20261002 (same ORDER_IDs), which lets
-- SDIS prove the cause: old/new ratio is a constant ≈ 84 = the INR/USD rate.
--
-- Expected SDIS outcome (deterministic):
--   class = semantic (subtype unit_change, factor≈84 → INR→USD) · severity = SEV1
--   route = quarantine_and_confirm
--   Gate: load QUARANTINED. Diplomat asks the producer to confirm the currency.
--   After confirmation, Surgeon opens a PR that normalises the column (amount * fx).
-- =============================================================================
use role SDIS_PRODUCER_ROLE;
use warehouse SDIS_WH;
use schema SDIS_DB.RAW;

-- New orders, now in USD
insert into ORDERS (ORDER_ID, CUST_ID, ORDER_TS, AMOUNT, STATUS, CHANNEL, _LOAD_ID, _LOADED_AT)
select
    21000 + row_number() over (order by seq4()),
    uniform(1, 2000, random()),
    dateadd(second, uniform(0, 86399, random()), '2026-10-03'::timestamp_ntz),
    round(greatest(149, normal(2400, 900, random())) / 84.0, 2),
    iff(uniform(0, 9, random()) < 8, 'C', 'A'),
    array_construct('web','app','store')[uniform(0, 2, random())]::varchar,
    'L20261003',
    current_timestamp()
from table(generator(rowcount => 2700));

-- Restated orders from yesterday, re-sent in USD
insert into ORDERS (ORDER_ID, CUST_ID, ORDER_TS, AMOUNT, STATUS, CHANNEL, _LOAD_ID, _LOADED_AT)
select ORDER_ID, CUST_ID, ORDER_TS, round(AMOUNT / 84.0, 2), 'C', CHANNEL, 'L20261003', current_timestamp()
from ORDERS
where _LOAD_ID = 'L20261002'
qualify row_number() over (order by ORDER_ID) <= 300;
