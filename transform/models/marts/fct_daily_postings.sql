-- New, open and closed jobs per day, role family and city. Built on a date
-- spine so days with no activity show zero. Fully remote jobs count under
-- the city Remote and jobs without a city under Unknown. Postings are
-- counted as jobs, so a job listed on two sources counts once. A job is new
-- on its opened_date, not on the day it was first seen, so the first run of
-- a new source or company does not show its old jobs as new.

with

jobs as (
    select * from {{ ref('dim_jobs') }}
),

-- From the first day a job was seen to the newest run
spine_bounds as (
    select
        min(first_seen) as first_day,
        greatest(max(last_seen), max(closed_date)) as last_day
    from jobs
),

spine as (
    select series.spine_date::date as spine_date
    from spine_bounds
    cross join lateral generate_series(
        spine_bounds.first_day::timestamp, spine_bounds.last_day::timestamp, interval '1 day'
    ) as series (spine_date)
),

-- A job that opened before tracking began is open from the first day, but
-- it is not new on any day, since the days it was new are not tracked
job_groups as (
    select
        jobs.job_id,
        jobs.role_family,
        jobs.closed_date,
        case when jobs.work_mode = 'remote' then 'Remote' else jobs.city end as city,
        greatest(jobs.opened_date, spine_bounds.first_day) as open_from,
        jobs.opened_date >= spine_bounds.first_day as is_new
    from jobs
    cross join spine_bounds
),

-- Every role family and city that ever had a job gets a row for every day
groups as (
    select distinct
        role_family,
        city
    from job_groups
),

opened_jobs as (
    select
        open_from as spine_date,
        role_family,
        city,
        count(*) filter (where is_new) as new_postings,
        count(*) as opened_postings
    from job_groups
    group by open_from, role_family, city
),

closed_jobs as (
    select
        closed_date as spine_date,
        role_family,
        city,
        count(*) as closed_postings
    from job_groups
    where closed_date is not null
    group by closed_date, role_family, city
),

daily as (
    select
        spine.spine_date,
        groups.role_family,
        groups.city,
        coalesce(opened_jobs.new_postings, 0)::integer as new_postings,
        coalesce(opened_jobs.opened_postings, 0)::integer as opened_postings,
        coalesce(closed_jobs.closed_postings, 0)::integer as closed_postings
    from spine
    cross join groups
    left join opened_jobs
        on
            spine.spine_date = opened_jobs.spine_date
            and groups.role_family = opened_jobs.role_family
            and groups.city = opened_jobs.city
    left join closed_jobs
        on
            spine.spine_date = closed_jobs.spine_date
            and groups.role_family = closed_jobs.role_family
            and groups.city = closed_jobs.city
),

-- A job is open from open_from until the day it closed, so the jobs open on
-- a day are the ones opened so far minus those closed so far
running as (
    select
        spine_date,
        role_family,
        city,
        new_postings,
        closed_postings,
        (sum(opened_postings) over so_far - sum(closed_postings) over so_far)::integer
            as open_postings
    from daily
    window so_far as (partition by role_family, city order by spine_date)
),

final as (
    select
        -- The dashboard reads this column as date, although it is a keyword
        spine_date as date,  -- noqa: RF04
        role_family,
        city,
        new_postings,
        open_postings,
        closed_postings
    from running
)

select * from final
