with changes as (
 select body->>'tenant' as tenant,body->>'entity' as entity,body->>'contract_id' as contract_id,
 cast(body->>'effective_start' as date) as effective_start,cast(body->>'effective_end' as date) as effective_end,
 cast(body->>'revision' as integer) as revision,
 cast(body->>'unit_monthly_cents' as bigint)*cast(body->>'quantity' as bigint) as monthly_cents,
 row_number() over(partition by body->>'tenant',body->>'entity',body->>'contract_id',body->>'change_id'
 order by cast(body->>'revision' as integer) desc) as known_rank
 from {{ source('raw','lifecycle') }}
 where cast(body->>'observed_source_at' as timestamptz)<=(select known_at from raw.reporting_period)
), periods as (
 select c.body->>'tenant' as tenant,c.body->>'entity' as entity,c.body->>'contract_id' as contract_id,
 m.date_day,m.valid_to,
 case when l.revision is not null then l.monthly_cents
 when m.date_day>=cast(c.body->>'effective_start' as date) and m.date_day<cast(c.body->>'effective_end' as date)
 then cast(c.body->>'unit_monthly_cents' as bigint)*cast(c.body->>'quantity' as bigint) else 0 end as monthly_cents,
 coalesce(l.revision,1) as source_revision,
 row_number() over(partition by c.body->>'tenant',c.body->>'entity',c.body->>'contract_id',m.date_day
 order by l.effective_start desc nulls last,l.revision desc nulls last) as rnk
 from {{ source('raw','commitments') }} c cross join raw.months m
 left join changes l on l.known_rank=1 and l.tenant=c.body->>'tenant' and l.entity=c.body->>'entity'
 and l.contract_id=c.body->>'contract_id' and m.date_day>=l.effective_start and m.date_day<l.effective_end
)
select tenant,entity,contract_id,date_day,valid_to,monthly_cents,source_revision from periods where rnk=1
