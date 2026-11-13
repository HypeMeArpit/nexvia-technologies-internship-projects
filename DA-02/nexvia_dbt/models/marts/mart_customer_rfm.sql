{{ config(materialized='table') }}

with order_items as (
    select * from {{ ref('stg_thelook__order_items') }}
    -- cancelled and returned items are not realised revenue
    where order_item_status not in ('Cancelled', 'Returned')
),

users as (
    select * from {{ ref('stg_thelook__users') }}
),

customer_orders as (
    select
        user_id,
        max(ordered_at) as last_order_at,
        count(distinct order_id) as frequency,
        sum(sale_price) as monetary_value
    from order_items
    group by user_id
),

as_of as (
    select date(max(last_order_at)) as as_of_date
    from customer_orders
)

select
    users.user_id,
    users.country,
    customer_orders.last_order_at,
    date_diff(as_of.as_of_date, date(customer_orders.last_order_at), day) as recency_days,
    coalesce(customer_orders.frequency, 0) as frequency,
    coalesce(customer_orders.monetary_value, 0) as historical_revenue,
    safe_divide(customer_orders.monetary_value, customer_orders.frequency) as avg_order_value,
    case
        when customer_orders.user_id is null then 'Never purchased'
        when date_diff(as_of.as_of_date, date(customer_orders.last_order_at), day) > 180 then 'Lapsed'
        when customer_orders.frequency >= 3 and customer_orders.monetary_value > 500 then 'VIP'
        when customer_orders.frequency >= 2 and customer_orders.monetary_value > 200 then 'Loyal'
        else 'Active'
    end as customer_segment
from users
cross join as_of
left join customer_orders
    on users.user_id = customer_orders.user_id