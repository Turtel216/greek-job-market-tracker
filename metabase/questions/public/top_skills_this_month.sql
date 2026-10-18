-- The 15 skills open jobs ask for most in the newest month, as a share of
-- all jobs open that month. jobs_total is the same for every skill in a
-- month and role family, so adding it up over role families gives the
-- month's total.
with

demand as (
    select
        demand.month,
        skills.name as skill,
        sum(demand.jobs_with_skill) as jobs_with_skill,
        sum(demand.jobs_total) as jobs_total
    from marts.fct_monthly_skill_demand as demand
    inner join marts.dim_skills as skills on demand.skill_id = skills.skill_id
    where
        demand.month = (select max(month) from marts.fct_monthly_skill_demand)
        [[and demand.role_family = {{role_family}}]]
    group by demand.month, skills.name
)

select
    skill,
    round(jobs_with_skill::numeric / jobs_total, 4) as share_of_jobs,
    jobs_with_skill
from demand
where jobs_with_skill > 0
order by jobs_with_skill desc, skill asc
limit 15
