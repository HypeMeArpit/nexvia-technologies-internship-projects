with source as (
    select * from {{ source('raw', 'ad_spend') }}
)

select
    cast(date as date) as spend_date,
    cast(campaign_channel as string) as campaign_channel,
    cast(spend as float64) as spend_amount,
    cast(impressions as int64) as impressions,
    cast(clicks as int64) as clicks
from source
