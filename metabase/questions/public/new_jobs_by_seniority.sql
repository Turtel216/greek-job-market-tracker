-- Jobs opened each month, by seniority. Jobs opened before tracking began
-- are left out, as they are in the new jobs per week chart.
select
    date_trunc('month', opened_date)::date as month,
    seniority,
    count(*) as jobs
from marts.dim_jobs
where
    opened_date >= (select min(first_seen) from marts.dim_jobs)
    [[and role_family = {{role_family}}]]
group by 1, 2
order by 1, array_position(array['intern', 'junior', 'mid', 'senior', 'lead', 'unknown'], seniority)
