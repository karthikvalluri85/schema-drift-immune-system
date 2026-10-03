select
    o.order_id,
    o.cust_id,
    c.segment,
    c.city,
    o.order_ts,
    o.order_ts::date            as order_date,
    o.amount_inr,
    o.status,
    o.channel
from {{ ref('stg_orders') }} o
left join {{ ref('stg_customers') }} c
       on c.cust_id = o.cust_id
