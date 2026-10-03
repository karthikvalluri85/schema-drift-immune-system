-- =============================================================================
-- Seed the "ShopVerse" Bronze tables with 7 clean daily loads.
-- Run as the simulated upstream producer. Re-running recreates the baseline.
--
-- Baseline contract (what downstream dbt models expect):
--   RAW.ORDERS    ORDER_ID, CUST_ID, ORDER_TS, AMOUNT NUMBER(10,2) [INR], STATUS ('A'|'C'), CHANNEL
--   RAW.CUSTOMERS CUST_ID, SEGMENT, CITY, SIGNUP_DATE
-- Every row carries _LOAD_ID / _LOADED_AT (append-only batches).
-- =============================================================================
use role SDIS_PRODUCER_ROLE;
use warehouse SDIS_WH;
use schema SDIS_DB.RAW;

create or replace table CUSTOMERS (
    CUST_ID      number(38,0)  not null,
    SEGMENT      varchar(20),
    CITY         varchar(40),
    SIGNUP_DATE  date,
    _LOAD_ID     varchar       not null,
    _LOADED_AT   timestamp_ltz not null
);

create or replace table ORDERS (
    ORDER_ID     number(38,0)  not null,
    CUST_ID      number(38,0)  not null,
    ORDER_TS     timestamp_ntz not null,
    AMOUNT       number(10,2)  not null,   -- INR
    STATUS       varchar(1)    not null,   -- 'A' = active, 'C' = completed
    CHANNEL      varchar(20),
    _LOAD_ID     varchar       not null,
    _LOADED_AT   timestamp_ltz not null
);

insert into CUSTOMERS
select
    row_number() over (order by seq4())                                        as cust_id,
    array_construct('CONSUMER','SMB','ENTERPRISE')[uniform(0, 2, random())]::varchar as segment,
    array_construct('Bengaluru','Mumbai','Delhi','Chennai','Hyderabad','Pune')[uniform(0, 5, random())]::varchar as city,
    dateadd(day, -uniform(30, 900, random()), '2026-09-25'::date)               as signup_date,
    'C20260925'                                                                 as _load_id,
    current_timestamp()                                                         as _loaded_at
from table(generator(rowcount => 2000));

execute immediate $$
declare
  load_date date;
  load_id varchar;
begin
  for i in 0 to 6 do
    load_date := dateadd(day, :i, '2026-09-26'::date);
    load_id   := 'L' || to_char(:load_date, 'YYYYMMDD');
    insert into ORDERS
    select
        :i * 3000 + row_number() over (order by seq4())                        as order_id,
        uniform(1, 2000, random())                                             as cust_id,
        dateadd(second, uniform(0, 86399, random()), :load_date::timestamp_ntz) as order_ts,
        round(greatest(149, normal(2400, 900, random())), 2)                   as amount,
        iff(uniform(0, 9, random()) < 8, 'C', 'A')                             as status,
        array_construct('web','app','store')[uniform(0, 2, random())]::varchar as channel,
        :load_id                                                               as _load_id,
        current_timestamp()                                                    as _loaded_at
    from table(generator(rowcount => 3000));
  end for;
  return 'seeded 7 loads';
end;
$$;

-- Agents need SELECT on the recreated tables (future grants cover new tables,
-- but re-grant explicitly to be safe after CREATE OR REPLACE).
grant select on table ORDERS    to role SDIS_AGENT_ROLE;
grant select on table CUSTOMERS to role SDIS_AGENT_ROLE;
grant select on table ORDERS    to role SDIS_TRANSFORM_ROLE;
grant select on table CUSTOMERS to role SDIS_TRANSFORM_ROLE;

select _load_id, count(*) as rows_, round(median(amount), 0) as median_amount_inr
from ORDERS group by 1 order by 1;
