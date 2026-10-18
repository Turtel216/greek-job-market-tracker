-- How many days jobs that have closed stayed open, by role family. Jobs
-- still open are left out, since their time open is not known yet.
select
    role_family,
    percentile_cont(0.5) within group (order by days_open) as median_days_open,
    count(*) as closed_jobs
from marts.dim_jobs
where
    not is_open
    [[and role_family = {{role_family}}]]
group by role_family
order by median_days_open desc, role_family asc
