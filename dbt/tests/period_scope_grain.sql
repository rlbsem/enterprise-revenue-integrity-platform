select entity from {{ ref('mart_period') }} group by tenant,entity having count(*)<>1
