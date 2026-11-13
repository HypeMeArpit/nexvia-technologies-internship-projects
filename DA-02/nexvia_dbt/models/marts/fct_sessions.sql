{{ config(materialized='table') }}

select
    session_id,
    user_id,
    user_id is not null as is_identified,
    date(session_start_at) as session_date,
    session_start_at,
    session_end_at,
    session_duration_seconds,
    traffic_source,
    ad_channel,
    events_count,
    has_purchase
from {{ ref('int_sessions') }}