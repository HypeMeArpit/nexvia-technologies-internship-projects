with events as (
    select * from {{ ref('int_events__user_resolved') }}
)

select
    session_id,
    max(resolved_user_id) as user_id,
    min(event_at) as session_start_at,
    max(event_at) as session_end_at,
    timestamp_diff(max(event_at), min(event_at), second) as session_duration_seconds,
    -- array_agg orders the sources by time and sequence, then we grab the very first one [offset(0)]
    array_agg(traffic_source order by event_at, sequence_number limit 1)[offset(0)] as traffic_source,
    array_agg(ad_channel order by event_at, sequence_number limit 1)[offset(0)] as ad_channel,
    count(event_id) as events_count,
    countif(event_type = 'purchase') > 0 as has_purchase
from events
group by session_id