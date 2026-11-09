-- A job has a closed_date exactly when it is closed, it closes after it was
-- last seen, it opened on or before the day it was first seen, and it is
-- listed on at least one source
select
    job_id,
    opened_date,
    first_seen,
    last_seen,
    closed_date,
    is_open,
    source_count
from {{ ref('dim_jobs') }}
where
    is_open = (closed_date is not null)
    or closed_date <= last_seen
    or opened_date > first_seen
    or days_open < 1
    or source_count < 1
