select 1
from (select sum(historical_revenue) as mart_total from {{ ref('mart_customer_rfm') }}),
     (select sum(sale_price) as source_total
      from {{ ref('stg_thelook__order_items') }}
      where order_item_status not in ('Cancelled', 'Returned'))
where abs(coalesce(mart_total, 0) - coalesce(source_total, 0)) > 0.01