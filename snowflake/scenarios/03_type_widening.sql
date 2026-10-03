-- =============================================================================
-- Scenario 3 — TYPE WIDENING drift
-- Enterprise bulk orders exceed NUMBER(10,2), so the producer widens AMOUNT to
-- NUMBER(18,2). (Snowflake allows increasing precision; it does not allow changing
-- scale in place — SDIS treats any scale *decrease* as narrowing = breaking.)
--
-- Expected SDIS outcome (deterministic):
--   class = type_widening · severity = SEV3 · route = contract_update_pr
--   Surgeon: PR updating the stg_orders contract data_type for AMOUNT; dbt build verifies.
--   Gate: load PASSED (widening is lossless for every existing value).
-- =============================================================================
use role SDIS_PRODUCER_ROLE;
use warehouse SDIS_WH;
use schema SDIS_DB.RAW;

alter table ORDERS alter column AMOUNT set data type number(18,2);

insert into ORDERS (ORDER_ID, CUST_ID, ORDER_TS, AMOUNT, STATUS, CHANNEL, _LOAD_ID, _LOADED_AT)
select
    21000 + row_number() over (order by seq4()),
    uniform(1, 2000, random()),
    dateadd(second, uniform(0, 86399, random()), '2026-10-03'::timestamp_ntz),
    iff(seq4() < 5, 150000000.00, round(greatest(149, normal(2400, 900, random())), 2)),
    iff(uniform(0, 9, random()) < 8, 'C', 'A'),
    array_construct('web','app','store')[uniform(0, 2, random())]::varchar,
    'L20261003',
    current_timestamp()
from table(generator(rowcount => 3000));
