-- Jobs first seen each month, by seniority
select
    date_trunc('month', first_seen)::date as month,
    seniority,
    count(*) as jobs
from marts.dim_jobs
where
    true
    [[and role_family = {{role_family}}]]
group by 1, 2
order by 1, array_position(array['intern', 'junior', 'mid', 'senior', 'lead', 'unknown'], seniority)
