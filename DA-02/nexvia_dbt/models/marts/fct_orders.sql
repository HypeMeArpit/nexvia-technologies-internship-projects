-- One row per order. Net revenue excludes cancelled and returned items,
-- because that money was never kept.

with orders as (
    select * from {{ ref('stg_thelook__orders') }}
),

order_items as (
    select * from {{ ref('stg_thelook__order_items') }}
),

order_revenue as (
    select
        order_id,
        sum(sale_price) as gross_revenue,
        sum(case when order_item_status not in ('Cancelled', 'Returned') then sale_price else 0 end) as net_revenue
    from order_items
    group by order_id
)

select
    orders.order_id,
    orders.user_id,
    orders.order_status,
    orders.ordered_at,
    date(orders.ordered_at) as order_date,
    orders.num_items,
    coalesce(order_revenue.gross_revenue, 0) as gross_revenue,
    coalesce(order_revenue.net_revenue, 0) as net_revenue
from orders
left join order_revenue
    on orders.order_id = order_revenue.order_id
