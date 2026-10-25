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
 where cast(body->>'observed_source_at' as timestamptz)<=(select known_at from raw.reporting_period)
)
select source,tenant,entity,event_id,logical_id,invoice_id,kind,revision,amount_cents,effective_date from known where rnk=1
and effective_date>=(select start_date from raw.reporting_period) and effective_date<(select end_exclusive from raw.reporting_period)
