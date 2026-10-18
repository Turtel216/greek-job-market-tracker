-- Share of all jobs seen that state a salary in EUR, on any basis
select
    round(
        count(*) filter (where salary_currency = 'EUR' and salary_min_monthly is not null)::numeric
        / nullif(count(*), 0),
        4
    ) as salary_stated_share
from marts.dim_jobs
where
    true
    [[and role_family = {{role_family}}]]
