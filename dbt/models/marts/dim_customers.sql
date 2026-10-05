select
    c.cust_id,
    c.segment,
    c.city,
    c.signup_date,
    count(o.order_id)                     as lifetime_orders,
    min(o.order_ts)::date                 as first_order_date,
    max(o.order_ts)::date                 as last_order_date
from {{ ref('stg_customers') }} c
left join {{ ref('stg_orders') }} o
       on o.cust_id = c.cust_id
group by 1, 2, 3, 4
