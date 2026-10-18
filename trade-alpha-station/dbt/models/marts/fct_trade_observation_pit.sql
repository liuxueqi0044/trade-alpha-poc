select * from {{ ref('stg_trade_observation') }} where observed_at <= cast('{{ var("as_of") }}' as timestamp)
