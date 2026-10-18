-- The newest run the marts were built from
select max(last_seen) as data_as_of
from marts.dim_jobs
