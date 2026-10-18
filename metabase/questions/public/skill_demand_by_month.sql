-- Share of open jobs asking for each of the 8 most asked for skills, month
-- by month
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
        true
        [[and demand.role_family = {{role_family}}]]
    group by demand.month, skills.name
),

top_skills as (
    select skill
    from demand
    group by skill
    having sum(jobs_with_skill) > 0
    order by sum(jobs_with_skill) desc, skill asc
    limit 8
)

select
    month,
    skill,
    round(jobs_with_skill::numeric / jobs_total, 4) as share_of_jobs
from demand
where skill in (select skill from top_skills)
order by month asc, skill asc
