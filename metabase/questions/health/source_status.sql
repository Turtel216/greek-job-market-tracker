-- Each source as of its newest successful run, and how long ago that run
-- loaded. Freshness warns at 26 hours.
with

newest_loads as (
    select
        source,
        max(load_finished_at) as load_finished_at
    from ops.ingest_runs
    where status = 'succeeded'
    group by source
)

select
    status.source,
    status.last_run_date,
    round((extract(epoch from now() - newest_loads.load_finished_at) / 3600)::numeric, 1)
        as hours_since_load,
    status.new_postings,
    status.closed_postings,
    status.open_postings
from qa.qa_source_status as status
left join newest_loads on status.source = newest_loads.source
order by status.source
