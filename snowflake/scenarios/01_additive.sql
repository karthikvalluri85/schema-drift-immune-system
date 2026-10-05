-- =============================================================================
-- Scenario 1 — ADDITIVE drift
-- Producer adds a new column DISCOUNT_CODE and lands load L20261003.
--
-- Expected SDIS outcome (deterministic):
--   class = additive · severity = SEV4 · route = auto_document
--   Surgeon: adds DISCOUNT_CODE to sources.yml tagged `sdis_new_unused`; no model change.
--   Gate: load PASSED immediately (nothing downstream can break).
-- =============================================================================
use role SDIS_PRODUCER_ROLE;
use warehouse SDIS_WH;
use schema SDIS_DB.RAW;

alter table ORDERS add column DISCOUNT_CODE varchar(20);

insert into ORDERS (ORDER_ID, CUST_ID, ORDER_TS, AMOUNT, STATUS, CHANNEL, DISCOUNT_CODE, _LOAD_ID, _LOADED_AT)
select
    21000 + row_number() over (order by seq4()),
    uniform(1, 2000, random()),
    dateadd(second, uniform(0, 86399, random()), '2026-10-03'::timestamp_ntz),
    round(greatest(149, normal(2400, 900, random())), 2),
    iff(uniform(0, 9, random()) < 8, 'C', 'A'),
    array_construct('web','app','store')[uniform(0, 2, random())]::varchar,
    iff(uniform(0, 9, random()) < 3, array_construct('DIWALI10','FESTIVE15','NEWUSER')[uniform(0, 2, random())]::varchar, null),
    'L20261003',
    current_timestamp()
from table(generator(rowcount => 3000));
