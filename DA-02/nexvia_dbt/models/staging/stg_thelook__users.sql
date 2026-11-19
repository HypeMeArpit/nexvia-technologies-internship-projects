with source as (
    select * from {{ source('thelook', 'users') }}
)

select
    cast(id as int64) as user_id,
    cast(age as int64) as age,
    cast(gender as string) as gender,
    cast(country as string) as country,
    cast(created_at as timestamp) as account_created_at
from source
