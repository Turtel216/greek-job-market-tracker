-- Salary text that gave no monthly amount. The reason says why: no amount
-- was found, the period is weekly, daily or hourly and is not converted,
-- or the monthly amount fell outside the salary_monthly_min and
-- salary_monthly_max bounds. One row per salary text and reason, the most
-- common first.

with

postings as (
    select * from {{ ref('stg_postings') }}
),

newest_run as (
    select max(last_run_date) as run_date from postings
),

failures as (
    select
        postings.salary_text,
        -- parse_salary sets the period only when it found an amount
        case
            when postings.salary_period is null then 'no amount'
            when postings.salary_period in ('weekly', 'daily', 'hourly') then 'period not converted'
            else 'out of bounds'
        end as reason,
        postings.last_run_date = newest_run.run_date as is_today
    from postings
    cross join newest_run
    where postings.salary_text is not null and postings.salary_min_monthly is null
),

final as (
    select
        salary_text,
        reason,
        count(*) as postings,
        count(*) filter (where is_today) as postings_today
    from failures
    group by salary_text, reason
)

select * from final
order by postings desc, salary_text asc
