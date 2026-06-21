-- Location text that matched no alias in city_aliases, so the posting's
-- city is Unknown. Add the common ones to the seed. Remote postings are
-- left out, since they need no city. One row per normalized location, the
-- most common first.

with

postings as (
    select * from {{ ref('stg_postings') }}
),

newest_run as (
    select max(last_run_date) as run_date from postings
),

unmapped as (
    select
        postings.location_text,
        {{ normalize_text('postings.location_text') }} as location_normalized,
        postings.last_run_date = newest_run.run_date as is_today
    from postings
    cross join newest_run
    where
        postings.city = 'Unknown'
        and postings.work_mode != 'remote'
        and {{ normalize_text('postings.location_text') }} is not null
),

final as (
    select
        location_normalized,
        min(location_text) as example_location,
        count(*) as postings,
        count(*) filter (where is_today) as postings_today
    from unmapped
    group by location_normalized
)

select * from final
order by postings desc, location_normalized asc
