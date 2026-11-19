-- The four attribution models side by side: one row per channel per model.
-- This long format is what the Tableau comparison chart uses.
-- ROAS = attributed revenue / spend. ROI = (attributed revenue - spend) / spend.
-- ROI here is revenue-based (no product costs), and spend is synthetic.

with unpivoted as (
    select
        channel,
        attribution_model,
        attributed_revenue,
        total_spend
    from {{ ref('mart_marketing_attribution') }}
    unpivot (
        attributed_revenue for attribution_model in (
            first_touch_revenue as 'First touch',
            last_touch_revenue as 'Last touch',
            linear_revenue as 'Linear',
            time_decay_revenue as 'Time decay'
        )
    )
)

select
    channel,
    attribution_model,
    attributed_revenue,
    total_spend,
    safe_divide(attributed_revenue, sum(attributed_revenue) over (partition by attribution_model)) as revenue_share,
    case when total_spend > 0 then attributed_revenue / total_spend end as roas,
    case when total_spend > 0 then (attributed_revenue - total_spend) / total_spend end as roi
from unpivoted
