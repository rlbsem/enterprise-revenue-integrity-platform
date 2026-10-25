{{ config(materialized='incremental',unique_key=['tenant','entity','invoice_id'],incremental_strategy='delete+insert') }}
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
          and cast(body->>'payment_date' as date)>=(select start_date from raw.reporting_period) and cast(body->>'payment_date' as date)<(select end_exclusive from raw.reporting_period)
     then cast(body->>'amount_cents' as bigint) else 0 end) as cash,
 sum(case when body->>'source'='netsuite' and body->>'kind'='payment_application'
          and cast(body->>'posting_date' as date)>=(select start_date from raw.reporting_period) and cast(body->>'posting_date' as date)<(select end_exclusive from raw.reporting_period)
     then cast(body->>'amount_cents' as bigint) else 0 end) as applications,
 sum(case when body->>'source'='netsuite' and body->>'kind' in ('posting','change_posting')
          and cast(body->>'posting_date' as date)>=(select start_date from raw.reporting_period) and cast(body->>'posting_date' as date)<(select end_exclusive from raw.reporting_period)
     and cast(coalesce(body->>'observed_source_at','2025-01-01T00:00:00+00:00') as timestamptz)<=(select known_at from raw.reporting_period)
     then cast(body->>'amount_cents' as bigint) else 0 end) as posting,
 sum(case when body->>'source'='stripe' and body->>'kind'='amendment_line' and cast(body->>'observed_source_at' as timestamptz)<=(select known_at from raw.reporting_period) then cast(body->>'amount_cents' as bigint) else 0 end) as amendments
 from {{ source('raw','records') }} group by 1,2,3
), valued as (
 select i.invoice_id,i.tenant,i.entity,i.currency,
 i.expected_base_cents+coalesce(a.proration_cents,0)-coalesce(a.credit_cents,0) as expected_invoice_cents,
 i.line_cents+coalesce(f.amendments,0)+coalesce(a.proration_cents,0)-coalesce(a.credit_cents,0) as billing_net_cents,
 coalesce(f.posting,0)+coalesce(a.erp_adjustment,0) as erp_net_cents,
 coalesce(f.cash,0) as allocated_cash_cents,coalesce(f.applications,0) as erp_applied_cash_cents,
 coalesce(a.credit_cents,0) as credit_cents,coalesce(a.proration_cents,0) as proration_cents
 from {{ ref('fct_invoices') }} i left join adjustments a using(tenant,entity,invoice_id)
 left join flows f using(tenant,entity,invoice_id)
)
select *,billing_net_cents-expected_invoice_cents as billing_contract_variance_cents,
 erp_net_cents-billing_net_cents as posting_variance_cents,
 erp_applied_cash_cents-allocated_cash_cents as cash_application_variance_cents from valued
