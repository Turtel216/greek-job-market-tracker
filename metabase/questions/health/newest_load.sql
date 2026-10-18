-- The newest run_date any source loaded successfully
select max(run_date) as newest_load
from ops.ingest_runs
where status = 'succeeded'
