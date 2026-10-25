with flow as (
 select tenant,entity,currency,sum(expected_invoice_cents) as expected_invoice_cents,
 sum(billing_net_cents) as billing_net_cents,sum(erp_net_cents) as erp_net_cents,
 sum(allocated_cash_cents) as allocated_cash_cents
 from {{ ref('mart_reconciliation') }} group by 1,2,3
), arr as (
 select i.body->>'tenant' as tenant,i.body->>'entity' as entity,
 sum(cast(i.body->>'monthly_cents' as bigint)*12) as period_end_arr_cents
 from {{ source('raw','records') }} i join {{ source('raw','records') }} s
 on s.body->>'id'=i.body->>'subscription_id' and s.body->>'tenant'=i.body->>'tenant' and s.body->>'entity'=i.body->>'entity'
 where i.body->>'source'='stripe' and i.body->>'kind'='subscription_item'
 and s.body->>'source'='stripe' and s.body->>'kind'='subscription'
 and s.body->>'status'='active'
 and cast(s.body->>'effective_start' as date)<=date '2026-03-31'
 and cast(s.body->>'effective_end' as date)>date '2026-03-31'
 group by 1,2
)
select f.*,coalesce(a.period_end_arr_cents,0) as period_end_arr_cents
from flow f left join arr a using(tenant,entity)
