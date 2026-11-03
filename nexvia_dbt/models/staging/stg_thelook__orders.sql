with source as (
    select * from {{ source('thelook', 'orders') }}
)

select
    order_id,
    user_id,
    status as order_status,
    created_at as ordered_at,
    returned_at,
    shipped_at,
    delivered_at,
    num_of_item as num_items
from source