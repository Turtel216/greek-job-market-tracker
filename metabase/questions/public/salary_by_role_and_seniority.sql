-- Monthly salary in EUR of every job seen that states one, by role family
-- and seniority. Annual pay is divided by 14 payments. Net amounts stay
-- net, so pick one basis at a time.
select
    role_family,
    seniority,
    count(*) as jobs,
    round(percentile_cont(0.5) within group (order by salary_min_monthly)) as median_from,
    round(percentile_cont(0.5) within group (order by salary_max_monthly)) as median_to,
    min(salary_min_monthly) as lowest,
    max(salary_max_monthly) as highest
from marts.dim_jobs
where
    salary_currency = 'EUR'
    and salary_min_monthly is not null
    [[and salary_basis = {{salary_basis}}]]
    [[and role_family = {{role_family}}]]
group by role_family, seniority
order by
    role_family,
    array_position(array['intern', 'junior', 'mid', 'senior', 'lead', 'unknown'], seniority)
