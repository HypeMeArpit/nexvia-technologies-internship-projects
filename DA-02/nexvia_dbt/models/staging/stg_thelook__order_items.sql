with source as (
    select * from {{ source('thelook', 'order_items') }}
)

select
    id as order_item_id,
    order_id,
    user_id,
    product_id,
    status as order_item_status,
    created_at as ordered_at,
    returned_at,
    sale_price
from source