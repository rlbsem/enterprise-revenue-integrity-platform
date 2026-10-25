with records as (
 select body from {{ source('raw','records') }} where body->>'source'='stripe'
), invoices as (
 select body->>'id' as invoice_id,body->>'tenant' as tenant,body->>'entity' as entity,
 body->>'currency' as currency,body->>'subscription_id' as subscription_id,
 cast(body->>'amount_cents' as bigint) as header_cents
 from records where body->>'kind'='invoice'
 and cast(body->>'invoice_date' as date)>=(select start_date from raw.reporting_period)
 and cast(body->>'invoice_date' as date)<(select end_exclusive from raw.reporting_period)
), lines as (
 select body->>'invoice_id' as invoice_id,sum(cast(body->>'amount_cents' as bigint)) as line_cents
 from records where body->>'kind'='invoice_line' group by 1
), subscriptions as (
 select body->>'id' as subscription_id,body->>'tenant' as tenant,body->>'entity' as entity,
 body->>'contract_id' as contract_id from records where body->>'kind'='subscription'
), commitments as (
 select tenant,entity,contract_id,sum(monthly_cents) as expected_base_cents
 from {{ ref('fct_contract_period') }} group by 1,2,3
)
select i.*,l.line_cents,c.expected_base_cents
from invoices i left join lines l using(invoice_id)
left join subscriptions s on i.subscription_id=s.subscription_id and i.tenant=s.tenant and i.entity=s.entity
left join commitments c on c.contract_id=s.contract_id and c.tenant=s.tenant and c.entity=s.entity
