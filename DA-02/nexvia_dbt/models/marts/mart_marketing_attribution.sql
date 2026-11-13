{{ config(materialized='table') }}

{#
  mart_marketing_attribution

  One row per channel: spend, first-touch and last-touch revenue, cost per
  acquisition, ROAS and ROI. Everything is measured over one shared date window:
  the ad spend dates, trimmed to the last day where sessions AND orders exist.

  ROI here is revenue-based (not profit-based). Ad spend and channel traffic are
  synthetic, so results are illustrative, not business findings.
#}

{# ---- settings: adjust these to match your project ---- #}
{% set spend_date_col = 'spend_date' %}  {# date column in stg_ad_spend #}
{% set lookback_days = 30 %}             {# last touch: how far before an order to look for a session #}
{% set order_grace_hours = 0 %}          {# let a session start this many hours AFTER the order timestamp #}

with ad_spend as (
    select * from {{ ref('stg_ad_spend') }}
),

-- One shared window: spend start, ending where sessions and orders also exist
spend_window as (
    select
        (select min(date(`{{ spend_date_col }}`)) from ad_spend) as window_start,
        least(
            (select max(date(`{{ spend_date_col }}`)) from ad_spend),
            (select max(date(session_start_at)) from {{ ref('int_sessions') }}),
            (select max(date(ordered_at)) from {{ ref('stg_thelook__order_items') }})
        ) as window_end
),

-- Identified sessions only (anonymous sessions cannot be tied to revenue)
sessions as (
    select
        user_id,
        session_start_at,
        ad_channel
    from {{ ref('int_sessions') }}
    where user_id is not null
),

-- Order-level revenue inside the window, excluding cancelled and returned items
orders_in_window as (
    select
        i.order_id,
        i.user_id,
        min(i.ordered_at) as ordered_at,
        sum(i.sale_price) as order_revenue
    from {{ ref('stg_thelook__order_items') }} i
    cross join spend_window w
    where i.order_item_status not in ('Cancelled', 'Returned')
      and date(i.ordered_at) between w.window_start and w.window_end
    group by i.order_id, i.user_id
),

-- First touch is computed over ALL history, so long-standing customers are not
-- credited to this month's ads
first_touch as (
    select
        user_id,
        min(session_start_at) as first_session_at,
        array_agg(ad_channel order by session_start_at asc limit 1)[offset(0)] as first_touch_channel
    from sessions
    group by user_id
),

-- Users whose first-ever session falls inside the window ("acquired in window")
new_users as (
    select
        f.user_id,
        f.first_touch_channel
    from first_touch f
    cross join spend_window w
    where date(f.first_session_at) between w.window_start and w.window_end
),

-- Last touch, per order: the latest identified session shortly before the order
order_last_touch as (
    select
        o.order_id,
        o.order_revenue,
        coalesce(
            array_agg(s.ad_channel order by s.session_start_at desc limit 1)[safe_offset(0)],
            'Unattributed'
        ) as channel
    from orders_in_window o
    left join sessions s
        on s.user_id = o.user_id
       and s.session_start_at <= timestamp_add(o.ordered_at, interval {{ order_grace_hours }} hour)
       and s.session_start_at >= timestamp_sub(o.ordered_at, interval {{ lookback_days }} day)
    group by o.order_id, o.order_revenue
),

first_touch_results as (
    select
        n.first_touch_channel as channel,
        sum(o.order_revenue) as first_touch_revenue,
        count(distinct n.user_id) as customers_acquired
    from new_users n
    inner join orders_in_window o
        on n.user_id = o.user_id
    group by channel
),

last_touch_results as (
    select
        channel,
        sum(order_revenue) as last_touch_revenue
    from order_last_touch
    group by channel
),

channel_spend as (
    select
        a.campaign_channel as channel,
        sum(a.spend_amount) as total_spend,
        sum(a.impressions) as total_impressions,
        sum(a.clicks) as total_clicks
    from ad_spend a
    cross join spend_window w
    where date(a.`{{ spend_date_col }}`) between w.window_start and w.window_end
    group by 1
),

all_channels as (
    select channel from first_touch_results
    union distinct
    select channel from last_touch_results
    union distinct
    select channel from channel_spend
),

joined as (
    select
        c.channel,
        coalesce(s.total_spend, 0) as total_spend,
        coalesce(s.total_impressions, 0) as total_impressions,
        coalesce(s.total_clicks, 0) as total_clicks,
        coalesce(f.customers_acquired, 0) as customers_acquired,
        coalesce(f.first_touch_revenue, 0) as first_touch_revenue,
        coalesce(l.last_touch_revenue, 0) as last_touch_revenue
    from all_channels c
    left join channel_spend s
        on c.channel = s.channel
    left join first_touch_results f
        on c.channel = f.channel
    left join last_touch_results l
        on c.channel = l.channel
)

select
    j.channel,
    w.window_start,
    w.window_end,
    j.total_spend,
    j.total_impressions,
    j.total_clicks,
    safe_divide(j.total_clicks, j.total_impressions) as ctr,
    j.customers_acquired,
    j.first_touch_revenue,
    j.last_touch_revenue,

    -- CPA = spend / customers acquired in the window
    safe_divide(j.total_spend, j.customers_acquired) as cost_per_acquisition,

    -- ROAS = revenue / spend
    safe_divide(j.first_touch_revenue, j.total_spend) as first_touch_roas,
    safe_divide(j.last_touch_revenue, j.total_spend) as last_touch_roas,

    -- ROI (on revenue) = (revenue - spend) / spend
    safe_divide(j.first_touch_revenue - j.total_spend, j.total_spend) as first_touch_roi,
    safe_divide(j.last_touch_revenue - j.total_spend, j.total_spend) as last_touch_roi
from joined j
cross join spend_window w