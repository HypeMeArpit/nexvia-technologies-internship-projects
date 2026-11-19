-- Daily spend, impressions and clicks per channel.
-- CTR is calculated in Tableau as SUM(clicks) / SUM(impressions), not stored per row,
-- so it can't be accidentally averaged.

select
    spend_date,
    campaign_channel,
    spend_amount,
    impressions,
    clicks
from {{ ref('stg_ad_spend') }}
