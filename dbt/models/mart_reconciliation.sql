with adjustments as (
 select tenant,entity,invoice_id,
 sum(case when source='stripe' and kind='credit' then amount_cents else 0 end) as credit_cents,
 sum(case when source='stripe' and kind='proration' then amount_cents else 0 end) as proration_cents,
 sum(case when source='netsuite' and kind='credit_posting' then -amount_cents
          when source='netsuite' and kind='proration_posting' then amount_cents else 0 end) as erp_adjustment
 from {{ ref('fct_adjustments') }} group by 1,2,3
), flows as (
 select body->>'tenant' as tenant,body->>'entity' as entity,body->>'invoice_id' as invoice_id,
 sum(case when body->>'source'='stripe' and body->>'kind'='payment_allocation'
          and cast(body->>'payment_date' as date)>=date '2026-01-01' and cast(body->>'payment_date' as date)<date '2026-04-01'
     then cast(body->>'amount_cents' as bigint) else 0 end) as cash,
 sum(case when body->>'source'='netsuite' and body->>'kind'='payment_application'
          and cast(body->>'posting_date' as date)>=date '2026-01-01' and cast(body->>'posting_date' as date)<date '2026-04-01'
     then cast(body->>'amount_cents' as bigint) else 0 end) as applications,
 sum(case when body->>'source'='netsuite' and body->>'kind'='posting'
          and cast(body->>'posting_date' as date)>=date '2026-01-01' and cast(body->>'posting_date' as date)<date '2026-04-01'
     then cast(body->>'amount_cents' as bigint) else 0 end) as posting
 from {{ source('raw','records') }} group by 1,2,3
), valued as (
 select i.invoice_id,i.tenant,i.entity,i.currency,
 i.expected_base_cents+coalesce(a.proration_cents,0)-coalesce(a.credit_cents,0) as expected_invoice_cents,
 i.line_cents+coalesce(a.proration_cents,0)-coalesce(a.credit_cents,0) as billing_net_cents,
 coalesce(f.posting,0)+coalesce(a.erp_adjustment,0) as erp_net_cents,
 coalesce(f.cash,0) as allocated_cash_cents,coalesce(f.applications,0) as erp_applied_cash_cents,
 coalesce(a.credit_cents,0) as credit_cents,coalesce(a.proration_cents,0) as proration_cents
 from {{ ref('fct_invoices') }} i left join adjustments a using(tenant,entity,invoice_id)
 left join flows f using(tenant,entity,invoice_id)
)
select *,billing_net_cents-expected_invoice_cents as billing_contract_variance_cents,
 erp_net_cents-billing_net_cents as posting_variance_cents,
 erp_applied_cash_cents-allocated_cash_cents as cash_application_variance_cents from valued
