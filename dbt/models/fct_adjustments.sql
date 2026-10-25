with known as (
 select body->>'source' as source,body->>'tenant' as tenant,body->>'entity' as entity,
 body->>'event_id' as event_id,body->>'logical_id' as logical_id,
 body->>'invoice_id' as invoice_id,body->>'kind' as kind,
 cast(body->>'revision' as integer) as revision,
 cast(body->>'amount_cents' as bigint) as amount_cents,
 cast(body->>'effective_date' as date) as effective_date,
 row_number() over(partition by body->>'source',body->>'tenant',body->>'entity',body->>'logical_id'
 order by cast(body->>'revision' as integer) desc) as rnk
 from {{ source('raw','adjustments') }}
 where cast(body->>'observed_source_at' as timestamptz)<=timestamptz '2026-03-31 23:59:59+00'
)
select * exclude(rnk) from known where rnk=1
and effective_date>=date '2026-01-01' and effective_date<date '2026-04-01'
