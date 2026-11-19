-- Each attribution model must share out exactly the revenue of the orders in the window,
-- and that revenue must not be zero (an empty window would otherwise pass silently).
-- Returns rows (= test fails) for any model that doesn't add up. The 0.10 tolerance
-- allows for each channel's revenue being rounded to 2 decimals.

with window_bounds as (
    select distinct window_start, window_end
    from {{ ref('mart_marketing_attribution') }}
),

window_revenue as (
    select sum(orders.net_revenue) as total
    from {{ ref('fct_orders') }} as orders
    cross join window_bounds
    where orders.net_revenue > 0
      and orders.order_date between window_bounds.window_start and window_bounds.window_end
),

model_totals as (
    select attribution_model, sum(attributed_revenue) as total
    from {{ ref('mart_attribution_by_model') }}
    group by attribution_model
)

select
    model_totals.attribution_model,
    model_totals.total as model_total,
    window_revenue.total as window_total
from model_totals
cross join window_revenue
where abs(coalesce(model_totals.total, 0) - coalesce(window_revenue.total, 0)) > 0.10
   or coalesce(window_revenue.total, 0) = 0
