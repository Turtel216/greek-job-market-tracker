-- Each source as of its newest successful run: the postings it saw for the
-- first time, the ones open now and the ones that run closed. The daily
-- summary and the health dashboard read it. A posting closes in the run
-- where its missed runs reach missed_runs_to_close, so each closing is
-- counted once, by the run that closed it.

with

lifecycle as (
    select * from {{ ref('int_posting_lifecycle') }}
),

ingest_runs as (
    select * from {{ source('ops', 'ingest_runs') }}
),

newest_runs as (
    select
        source,
        max(run_date) as last_run_date
    from ingest_runs
    where status = 'succeeded'
    group by source
),

final as (
    select
        newest_runs.source,
        newest_runs.last_run_date,
        count(lifecycle.posting_key) filter (
            where lifecycle.first_seen = newest_runs.last_run_date
        ) as new_postings,
        count(lifecycle.posting_key) filter (where lifecycle.is_open) as open_postings,
        count(lifecycle.posting_key) filter (
            where lifecycle.missed_runs = {{ var('missed_runs_to_close') }}
        ) as closed_postings
    from newest_runs
    left join lifecycle on newest_runs.source = lifecycle.source
    group by newest_runs.source, newest_runs.last_run_date
)

select * from final
