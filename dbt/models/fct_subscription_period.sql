with changes as (
 select body->>'tenant' as tenant,body->>'entity' as entity,body->>'contract_id' as contract_id,
 cast(body->>'effective_start' as date) as effective_start,cast(body->>'effective_end' as date) as effective_end,
 cast(body->>'revision' as integer) as revision,
 cast(body->>'unit_monthly_cents' as bigint)*cast(body->>'quantity' as bigint) as monthly_cents,
 row_number() over(partition by body->>'tenant',body->>'entity',body->>'contract_id',body->>'change_id'
 order by cast(body->>'revision' as integer) desc) as known_rank
 from {{ source('raw','subscription_changes') }}
 where cast(body->>'observed_source_at' as timestamptz)<=(select known_at from raw.reporting_period)
), periods as (
 select s.body->>'tenant' as tenant,s.body->>'entity' as entity,s.body->>'contract_id' as contract_id,
 s.body->>'id' as subscription_id,m.date_day,m.valid_to,
 coalesce(l.monthly_cents,cast(i.body->>'monthly_cents' as bigint)) as monthly_cents,
 coalesce(l.revision,1) as source_revision,
 row_number() over(partition by s.body->>'id',m.date_day order by l.effective_start desc nulls last,l.revision desc nulls last) as rnk
 from {{ source('raw','records') }} s join {{ source('raw','records') }} i
 on i.body->>'subscription_id'=s.body->>'id' and i.body->>'source'='stripe' and i.body->>'kind'='subscription_item'
 cross join raw.months m left join changes l on l.known_rank=1
 and l.tenant=s.body->>'tenant' and l.entity=s.body->>'entity' and l.contract_id=s.body->>'contract_id'
 and m.date_day>=l.effective_start and m.date_day<l.effective_end
 where s.body->>'source'='stripe' and s.body->>'kind'='subscription'
 and m.date_day>=cast(s.body->>'effective_start' as date) and m.date_day<cast(s.body->>'effective_end' as date)
)
select tenant,entity,contract_id,subscription_id,date_day,valid_to,monthly_cents,source_revision from periods where rnk=1
