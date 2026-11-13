with source as (
    select * from {{ source('thelook', 'events') }}
)

select
    id as event_id,
    user_id,
    session_id,
    sequence_number,
    created_at as event_at,
    traffic_source,
    uri as event_uri,
    event_type
from source