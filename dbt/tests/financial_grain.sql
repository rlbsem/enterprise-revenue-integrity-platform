select invoice_id from {{ ref('fct_invoices') }} where header_cents<>line_cents or currency<>'USD'
union all
select invoice_id from {{ ref('mart_reconciliation') }} where billing_net_cents<0 or allocated_cash_cents<0
or allocated_cash_cents>billing_net_cents
