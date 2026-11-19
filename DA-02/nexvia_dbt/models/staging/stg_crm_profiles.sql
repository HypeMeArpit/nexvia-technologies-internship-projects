with source as (
    select * from {{ source('raw', 'crm_profiles') }}
)

select
    cast(user_id as int64) as user_id,
    cast(customer_persona as string) as customer_persona,
    cast(account_manager as string) as account_manager,
    cast(loyalty_score as int64) as loyalty_score
from source
