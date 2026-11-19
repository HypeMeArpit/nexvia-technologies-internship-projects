-- One row per user: RFM scores, customer lifetime value (CLV) and a segment.
--
-- Scores run from 1 (worst) to 5 (best):
--   recency   -> quintiles of days since last order (most recent = 5)
--   frequency -> order-count buckets (1, 2, 3, 4, 5+). Most customers have 1-2 orders,
--                so quintiles would split identical customers into different scores.
--   monetary  -> quintiles of net revenue
-- Only orders with net revenue count (fully cancelled or returned orders are ignored).

with orders as (
    select * from {{ ref('fct_orders') }}
    where net_revenue > 0
),

users as (
    select * from {{ ref('stg_thelook__users') }}
),

as_of as (
    -- "today" for this analysis = the latest order date in the data
    select max(order_date) as as_of_date
    from orders
),

customer_orders as (
    select
        user_id,
        min(order_date) as first_order_date,
        max(order_date) as last_order_date,
        count(*) as frequency,
        sum(net_revenue) as monetary_value
    from orders
    group by user_id
),

customer_metrics as (
    select
        customer_orders.*,
        date_diff(as_of.as_of_date, last_order_date, day) as recency_days,
        date_diff(as_of.as_of_date, first_order_date, day) as tenure_days,
        monetary_value / frequency as avg_order_value
    from customer_orders
    cross join as_of
),

scored as (
    select
        *,
        ntile(5) over (order by recency_days desc, user_id) as r_score,
        least(frequency, 5) as f_score,
        ntile(5) over (order by monetary_value, user_id) as m_score
    from customer_metrics
)

select
    users.user_id,
    users.country,
    scored.first_order_date,
    scored.last_order_date,
    scored.recency_days,
    coalesce(scored.frequency, 0) as frequency,
    coalesce(scored.monetary_value, 0) as historical_revenue,
    scored.avg_order_value,
    scored.r_score,
    scored.f_score,
    scored.m_score,
    concat(cast(scored.r_score as string), cast(scored.f_score as string), cast(scored.m_score as string)) as rfm_score,

    -- Predicted 12-month CLV = average order value x orders per year.
    -- Tenure is floored at one year so a brand-new customer with one order
    -- isn't projected to order every month.
    round(
        scored.avg_order_value * scored.frequency * 365 / greatest(scored.tenure_days, 365),
        2
    ) as predicted_clv_12m,

    case
        when scored.user_id is null then 'Never purchased'
        when scored.r_score >= 4 and scored.f_score >= 3 then 'Champions'
        when scored.f_score >= 3 then 'Loyal'
        when scored.r_score <= 2 and scored.m_score >= 4 then 'At Risk'
        when scored.r_score >= 4 then 'Promising'
        else 'Hibernating'
    end as customer_segment
from users
left join scored
    on users.user_id = scored.user_id
