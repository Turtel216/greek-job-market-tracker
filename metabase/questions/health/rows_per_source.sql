-- Postings parsed per source and day over the last 60 days, from the
-- newest load of each day. A sudden drop usually means a parser broke.
with

loads as (
    select distinct on (source, run_date)
        source,
        run_date,
        rows_parsed
    from ops.ingest_runs
    where
        status = 'succeeded'
        and run_date > current_date - 60
    order by source, run_date, load_finished_at desc
)

select
    run_date,
    source,
    rows_parsed
from loads
order by run_date, source
