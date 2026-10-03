select
    cust_id,
    segment,
    count(*)                         as orders,
    sum(amount_inr)                  as ltv_inr,
    avg(amount_inr)                  as avg_order_value_inr
from {{ ref('fct_orders') }}
where status = 'C'
group by 1, 2
