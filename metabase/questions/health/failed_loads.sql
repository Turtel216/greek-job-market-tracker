-- Loads that failed in the last 30 days
select count(*) as failed_loads
from ops.ingest_runs
where
    status = 'failed'
    and run_date > current_date - 30
