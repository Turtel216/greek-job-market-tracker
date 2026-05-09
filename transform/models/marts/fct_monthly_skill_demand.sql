-- How many jobs ask for each skill per month and role family, and what
-- share of the jobs that is. A job counts in every month it was open: from
-- the month it was first seen to the month before it closed, or to the
-- newest run while it is still open.

with

jobs as (
    select * from {{ ref('dim_jobs') }}
),

job_skills as (
    select * from {{ ref('bridge_job_skills') }}
),

skills as (
    select * from {{ ref('dim_skills') }}
),

spine_bounds as (
    select
        min(first_seen) as first_day,
        greatest(max(last_seen), max(closed_date)) as last_day
    from jobs
),

months as (
    select series.month_start::date as month_start
    from spine_bounds
    cross join lateral generate_series(
        date_trunc('month', spine_bounds.first_day::timestamp),
        date_trunc('month', spine_bounds.last_day::timestamp),
        interval '1 month'
    ) as series (month_start)
),

-- A job is open from first_seen until the day before closed_date
job_months as (
    select
        jobs.job_id,
        jobs.role_family,
        months.month_start
    from jobs
    inner join months
        on
            jobs.first_seen < months.month_start + interval '1 month'
            and (jobs.closed_date is null or jobs.closed_date > months.month_start)
),

totals as (
    select
        month_start,
        role_family,
        count(*)::integer as jobs_total
    from job_months
    group by month_start, role_family
),

skill_counts as (
    select
        job_months.month_start,
        job_months.role_family,
        job_skills.skill_id,
        count(*) as jobs_with_skill
    from job_months
    inner join job_skills on job_months.job_id = job_skills.job_id
    group by job_months.month_start, job_months.role_family, job_skills.skill_id
),

-- Every skill gets a row for every month and role family with open jobs,
-- so a skill nobody asked for shows zero
final as (
    select
        -- The dashboard reads this column as month, although it is a keyword
        totals.month_start as month,  -- noqa: RF04
        skills.skill_id,
        totals.role_family,
        totals.jobs_total,
        coalesce(skill_counts.jobs_with_skill, 0)::integer as jobs_with_skill,
        round(coalesce(skill_counts.jobs_with_skill, 0)::numeric / totals.jobs_total, 4)
            as share_of_jobs
    from totals
    cross join skills
    left join skill_counts
        on
            totals.month_start = skill_counts.month_start
            and totals.role_family = skill_counts.role_family
            and skills.skill_id = skill_counts.skill_id
)

select * from final
