-- Customer 360: one row per user combining demographics (thelook), CRM attributes
-- (synthetic), acquisition channel (web sessions) and value (RFM + CLV).

with users as (
    select * from {{ ref('stg_thelook__users') }}
),

crm as (
    select * from {{ ref('stg_crm_profiles') }}
),

rfm as (
    select * from {{ ref('mart_customer_rfm') }}
),

-- The channel of each user's first identified web session
acquisition as (
    select
        user_id,
        min(session_start_at) as first_session_at,
        array_agg(ad_channel order by session_start_at limit 1)[offset(0)] as acquisition_channel
    from {{ ref('int_sessions') }}
    where user_id is not null
    group by user_id
)

select
    users.user_id,
    users.age,
    users.gender,
    users.country,
    users.account_created_at,
    crm.customer_persona,
    crm.account_manager,
    crm.loyalty_score,
    acquisition.first_session_at,
    acquisition.acquisition_channel,
    rfm.frequency,
    rfm.historical_revenue,
    rfm.avg_order_value,
    rfm.recency_days,
    rfm.r_score,
    rfm.f_score,
    rfm.m_score,
    rfm.rfm_score,
    rfm.predicted_clv_12m,
    rfm.customer_segment
from users
left join crm
    on users.user_id = crm.user_id
left join acquisition
    on users.user_id = acquisition.user_id
left join rfm
    on users.user_id = rfm.user_id
