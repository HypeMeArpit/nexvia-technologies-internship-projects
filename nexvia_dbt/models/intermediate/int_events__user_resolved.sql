{{ config(materialized='table') }}

with events as (
    select * from {{ ref('stg_thelook__events') }}
),

channel_mapping as (
    select * from {{ ref('channel_mapping') }}
),

session_users as (
    select
        session_id,
        max(user_id) as resolved_user_id,
        count(distinct user_id) as distinct_user_ids
    from events
    group by session_id
),

final as (
    select
        events.event_id,
        events.session_id,
        events.user_id as original_user_id,
        session_users.resolved_user_id,
        session_users.distinct_user_ids,
        events.sequence_number,
        events.event_at,
        events.traffic_source,
        channel_mapping.ad_channel,
        events.event_uri,
        events.event_type
    from events
    left join session_users
        on events.session_id = session_users.session_id
    left join channel_mapping
        on events.traffic_source = channel_mapping.traffic_source
)

select * from final