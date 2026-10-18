select * from {{ source('bronze', 'trade_observation') }}
