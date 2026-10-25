select contract_id from {{ ref('fct_contract_period') }} group by tenant,entity,contract_id,date_day having count(*)<>1
union all
select subscription_id from {{ ref('fct_subscription_period') }} group by tenant,entity,subscription_id,date_day having count(*)<>1
