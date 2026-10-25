select logical_id from {{ ref('fct_adjustments') }} group by source,tenant,entity,logical_id having count(*)<>1
