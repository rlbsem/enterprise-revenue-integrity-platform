with flow as (
 select tenant,entity,currency,sum(expected_invoice_cents) as expected_invoice_cents,
 sum(billing_net_cents) as billing_net_cents,sum(erp_net_cents) as erp_net_cents,
 sum(allocated_cash_cents) as allocated_cash_cents
 from {{ ref('mart_reconciliation') }} group by 1,2,3
), arr as (
 select tenant,entity,sum(monthly_cents*12) as period_end_arr_cents
 from {{ ref('fct_subscription_period') }} where valid_to=(select end_exclusive from raw.reporting_period)
 group by 1,2
)
select f.*,coalesce(a.period_end_arr_cents,0) as period_end_arr_cents
from flow f left join arr a using(tenant,entity)
