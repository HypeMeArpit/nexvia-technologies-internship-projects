select 1
from (select countif(has_purchase) as purchase_sessions from {{ ref('int_sessions') }})
where purchase_sessions = 0