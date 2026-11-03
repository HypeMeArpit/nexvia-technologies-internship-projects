select session_id
from {{ ref('int_events__user_resolved') }}
group by session_id
having max(distinct_user_ids) > 1