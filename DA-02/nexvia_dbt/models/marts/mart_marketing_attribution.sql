-- One row per channel: spend, revenue under four attribution models, CPA, ROAS and ROI.
--
-- Every model shares out the SAME revenue (net revenue of orders placed in the ad-spend
-- window), so the four columns are directly comparable and each sums to the same total.
--   First touch : 100% to the channel of the customer's earliest session before the order
--   Last touch  : 100% to the latest session in the 30 days before the order
--   Linear      : equal share to every session in the 30 days before the order
--   Time decay  : like linear, but a session's weight halves every 7 days before the order
-- Orders with no qualifying session are credited to 'Unattributed'.
--
-- Ad spend is synthetic, so ROI and CPA demonstrate the method rather than real performance.

with ad_spend as (
    select * from {{ ref('stg_ad_spend') }}
),

-- The analysis window: the ad-spend dates, cut off at the last day that also has sessions and orders
spend_window as (
    select
        (select min(spend_date) from ad_spend) as window_start,
        least(
            (select max(spend_date) from ad_spend),
            (select max(date(session_start_at)) from {{ ref('int_sessions') }}),
            (select max(order_date) from {{ ref('fct_orders') }})
        ) as window_end
),

-- Identified sessions only: an anonymous session can't be linked to an order
sessions as (
    select
        user_id,
        session_start_at,
        coalesce(ad_channel, 'Unknown') as ad_channel
    from {{ ref('int_sessions') }}
    where user_id is not null
),

orders_in_window as (
    select
        orders.order_id,
        orders.user_id,
        orders.ordered_at,
        orders.net_revenue as order_revenue
    from {{ ref('fct_orders') }} as orders
    cross join spend_window
    where orders.net_revenue > 0
      and orders.order_date between spend_window.window_start and spend_window.window_end
),

-- First touch: the customer's earliest session before each order (no lookback limit)
first_touch as (
    select
        orders_in_window.order_id,
        array_agg(sessions.ad_channel order by sessions.session_start_at limit 1)[offset(0)] as channel
    from orders_in_window
    inner join sessions
        on sessions.user_id = orders_in_window.user_id
       and sessions.session_start_at <= orders_in_window.ordered_at
    group by orders_in_window.order_id
),

-- All sessions in the 30 days before each order (used by last touch, linear and time decay)
touches as (
    select
        orders_in_window.order_id,
        sessions.ad_channel,
        sessions.session_start_at,
        date_diff(date(orders_in_window.ordered_at), date(sessions.session_start_at), day) as days_before_order
    from orders_in_window
    inner join sessions
        on sessions.user_id = orders_in_window.user_id
       and sessions.session_start_at <= orders_in_window.ordered_at
       and sessions.session_start_at >= timestamp_sub(orders_in_window.ordered_at, interval 30 day)
),

-- Each session's share of its order, under each model. Shares add up to 1 per order.
touch_weights as (
    select
        order_id,
        ad_channel,
        case
            when row_number() over (partition by order_id order by session_start_at desc, ad_channel) = 1 then 1.0
            else 0.0
        end as last_touch_weight,
        1.0 / count(*) over (partition by order_id) as linear_weight,
        pow(0.5, days_before_order / 7) / sum(pow(0.5, days_before_order / 7)) over (partition by order_id) as time_decay_weight
    from touches
),

-- Revenue credited to each channel, one row per order and channel
credited as (
    select
        touch_weights.ad_channel as channel,
        0.0 as first_touch_revenue,
        orders_in_window.order_revenue * touch_weights.last_touch_weight as last_touch_revenue,
        orders_in_window.order_revenue * touch_weights.linear_weight as linear_revenue,
        orders_in_window.order_revenue * touch_weights.time_decay_weight as time_decay_revenue
    from touch_weights
    inner join orders_in_window
        on touch_weights.order_id = orders_in_window.order_id

    union all

    select
        first_touch.channel,
        orders_in_window.order_revenue,
        0.0,
        0.0,
        0.0
    from first_touch
    inner join orders_in_window
        on first_touch.order_id = orders_in_window.order_id

    union all

    -- orders with no session in the 30-day lookback
    select
        'Unattributed',
        0.0,
        order_revenue,
        order_revenue,
        order_revenue
    from orders_in_window
    where order_id not in (select order_id from touches)

    union all

    -- orders with no session at all before the order
    select
        'Unattributed',
        order_revenue,
        0.0,
        0.0,
        0.0
    from orders_in_window
    where order_id not in (select order_id from first_touch)
),

attributed_revenue as (
    select
        channel,
        sum(first_touch_revenue) as first_touch_revenue,
        sum(last_touch_revenue) as last_touch_revenue,
        sum(linear_revenue) as linear_revenue,
        sum(time_decay_revenue) as time_decay_revenue
    from credited
    group by channel
),

-- New customers: first-ever session inside the window AND an order inside the window
user_first_session as (
    select
        user_id,
        min(session_start_at) as first_session_at,
        array_agg(ad_channel order by session_start_at limit 1)[offset(0)] as first_channel
    from sessions
    group by user_id
),

new_customers as (
    select
        user_first_session.first_channel as channel,
        count(*) as customers_acquired
    from user_first_session
    cross join spend_window
    where date(user_first_session.first_session_at) between spend_window.window_start and spend_window.window_end
      and user_first_session.user_id in (select user_id from orders_in_window)
    group by user_first_session.first_channel
),

channel_spend as (
    select
        ad_spend.campaign_channel as channel,
        sum(ad_spend.spend_amount) as total_spend,
        sum(ad_spend.impressions) as total_impressions,
        sum(ad_spend.clicks) as total_clicks
    from ad_spend
    cross join spend_window
    where ad_spend.spend_date between spend_window.window_start and spend_window.window_end
    group by ad_spend.campaign_channel
),

all_channels as (
    select channel from attributed_revenue
    union distinct
    select channel from new_customers
    union distinct
    select channel from channel_spend
)

select
    all_channels.channel,
    spend_window.window_start,
    spend_window.window_end,
    coalesce(channel_spend.total_spend, 0) as total_spend,
    coalesce(channel_spend.total_impressions, 0) as total_impressions,
    coalesce(channel_spend.total_clicks, 0) as total_clicks,
    safe_divide(channel_spend.total_clicks, channel_spend.total_impressions) as ctr,
    coalesce(new_customers.customers_acquired, 0) as customers_acquired,
    round(coalesce(attributed_revenue.first_touch_revenue, 0), 2) as first_touch_revenue,
    round(coalesce(attributed_revenue.last_touch_revenue, 0), 2) as last_touch_revenue,
    round(coalesce(attributed_revenue.linear_revenue, 0), 2) as linear_revenue,
    round(coalesce(attributed_revenue.time_decay_revenue, 0), 2) as time_decay_revenue,

    -- CPA = spend / new customers. Left empty for unpaid channels (spend = 0).
    case
        when channel_spend.total_spend > 0
            then safe_divide(channel_spend.total_spend, new_customers.customers_acquired)
    end as cost_per_acquisition
from all_channels
cross join spend_window
left join channel_spend
    on all_channels.channel = channel_spend.channel
left join new_customers
    on all_channels.channel = new_customers.channel
left join attributed_revenue
    on all_channels.channel = attributed_revenue.channel
