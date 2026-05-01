-- One row per real-world job. The title and attributes come from the job's
-- primary posting, the salary from its first posting that states one, and
-- the dates from all of its postings. See "Marts" in the design doc.

{%- set ats_sources = "'" ~ var('ats_sources') | join("', '") ~ "'" %}

with

postings as (
    select * from {{ ref('stg_postings') }}
),

lifecycle as (
    select * from {{ ref('int_posting_lifecycle') }}
),

job_matches as (
    select * from {{ ref('int_job_matches') }}
),

members as (
    select
        job_matches.job_id,
        postings.posting_key,
        postings.source,
        postings.title,
        postings.company_name,
        postings.role_family,
        postings.seniority,
        postings.work_mode,
        postings.city,
        postings.region,
        postings.salary_min_monthly,
        postings.salary_max_monthly,
        postings.salary_currency,
        postings.salary_basis,
        lifecycle.first_seen,
        lifecycle.last_seen,
        lifecycle.is_open,
        lifecycle.closed_date,
        postings.source in ({{ ats_sources }}) as is_ats,
        -- Attributes the posting knows. Defaults such as Unknown do not count.
        (postings.title is not null)::integer
        + (postings.company_name is not null)::integer
        + (postings.city != 'Unknown')::integer
        + (postings.work_mode != 'unknown')::integer
        + (postings.seniority != 'unknown')::integer
        + (postings.role_family != 'other')::integer
        + (postings.salary_min_monthly is not null)::integer as known_attributes
    from job_matches
    inner join postings on job_matches.posting_key = postings.posting_key
    inner join lifecycle on job_matches.posting_key = lifecycle.posting_key
),

ranked as (
    select
        *,
        -- The primary posting: ATS over job board, then the one that knows
        -- the most, then the earliest seen. posting_key breaks any tie left.
        row_number() over (
            partition by job_id
            order by is_ats desc, known_attributes desc, first_seen asc, posting_key asc
        ) as primary_rank,
        -- The first posting that states a salary
        row_number() over (
            partition by job_id
            order by salary_min_monthly is null asc, first_seen asc, posting_key asc
        ) as salary_rank
    from members
),

primary_postings as (
    select * from ranked
    where primary_rank = 1
),

salaries as (
    select * from ranked
    where salary_rank = 1 and salary_min_monthly is not null
),

clusters as (
    select
        job_id,
        count(distinct source) as source_count,
        min(first_seen) as first_seen,
        max(last_seen) as last_seen,
        bool_or(is_open) as is_open,
        -- A job closes when its last open posting closes
        case when not bool_or(is_open) then max(closed_date) end as closed_date
    from members
    group by job_id
),

final as (
    select
        primary_postings.job_id,
        primary_postings.title,
        primary_postings.company_name,
        primary_postings.role_family,
        primary_postings.seniority,
        primary_postings.work_mode,
        primary_postings.city,
        primary_postings.region,
        salaries.salary_min_monthly,
        salaries.salary_max_monthly,
        salaries.salary_currency,
        salaries.salary_basis,
        clusters.source_count::integer as source_count,
        clusters.first_seen,
        clusters.last_seen,
        clusters.closed_date,
        clusters.is_open,
        clusters.last_seen - clusters.first_seen + 1 as days_open
    from primary_postings
    inner join clusters on primary_postings.job_id = clusters.job_id
    left join salaries on primary_postings.job_id = salaries.job_id
)

select * from final
