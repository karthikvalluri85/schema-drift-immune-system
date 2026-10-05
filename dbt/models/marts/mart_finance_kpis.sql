select
    order_date,
    sum(revenue_inr)                                  as gmv_inr,
    sum(orders)                                       as orders,
    sum(revenue_inr) / nullif(sum(orders), 0)         as aov_inr
from {{ ref('fct_revenue_daily') }}
group by 1
