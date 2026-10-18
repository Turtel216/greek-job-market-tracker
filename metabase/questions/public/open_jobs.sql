-- Jobs open now
select count(*) as open_jobs
from marts.dim_jobs
where
    is_open
    [[and role_family = {{role_family}}]]
