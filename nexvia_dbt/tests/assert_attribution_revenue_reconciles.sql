
with mart as (
    select
        sum(last_touch_revenue) as last_touch_total,
        sum(first_touch_revenue) as first_touch_total,
        min(window_start) as window_start,
        max(window_end) as window_end
    from {{ ref('mart_marketing_attribution') }}
),

window_orders as (
    select
        sum(i.sale_price) as window_revenue
    from {{ ref('stg_thelook__order_items') }} i
    cross join mart m
    where i.order_item_status not in ('Cancelled', 'Returned')
      and date(i.ordered_at) between m.window_start and m.window_end
)

select 1
from mart
cross join window_orders
where abs(mart.last_touch_total - window_orders.window_revenue) > 0.01
   or mart.first_touch_total > mart.last_touch_total + 0.01