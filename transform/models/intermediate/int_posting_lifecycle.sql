-- When each posting was first and last seen, and whether it is still open.
-- A posting closes once it is missing from missed_runs_to_close successful
-- runs of its source in a row. Failed runs do not count, so a broken
-- scraper never closes postings in bulk.

with

raw_postings as (
    select * from {{ ref('stg_raw_postings') }}
),

ingest_runs as (
    select * from {{ source('ops', 'ingest_runs') }}
),

-- A day is a successful run of a source when any load of it that day
-- succeeded. A failed extract writes no manifest, so it has no row at all.
successful_runs as (
    select distinct
        source,
        run_date
    from ingest_runs
    where status = 'succeeded'
),

sightings as (
    select
        posting_key,
        source,
        min(run_date) as first_seen,
        max(run_date) as last_seen
    from raw_postings
    group by posting_key, source
),

-- Successful runs of the source after the posting was last seen. A posting
-- missing from one of them is missing from every later one too, so these
-- runs are always in a row.
missed as (
    select
        sightings.posting_key,
        count(successful_runs.run_date) as missed_runs,
        min(successful_runs.run_date) as first_missed
    from sightings
    left join successful_runs
        on
            sightings.source = successful_runs.source
            and sightings.last_seen < successful_runs.run_date
    group by sightings.posting_key
),

final as (
    select
        sightings.posting_key,
        sightings.source,
        sightings.first_seen,
        sightings.last_seen,
        missed.missed_runs::integer as missed_runs,
        missed.missed_runs < {{ var('missed_runs_to_close') }} as is_open,
        -- The first day it was missing
        case
            when missed.missed_runs >= {{ var('missed_runs_to_close') }} then missed.first_missed
        end as closed_date
    from sightings
    inner join missed on sightings.posting_key = missed.posting_key
)

select * from final
