select
    order_date,
    channel,
    segment,
    count(*)                          as orders,
    sum(amount_inr)                   as revenue_inr,
    median(amount_inr)                as median_order_inr
from {{ ref('fct_orders') }}
where status = 'C'
group by 1, 2, 3
