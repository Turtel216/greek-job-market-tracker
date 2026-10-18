-- Share of open jobs that are fully remote, out of those whose work mode
-- is known
select
    round(
        count(*) filter (where work_mode = 'remote')::numeric
        / nullif(count(*) filter (where work_mode != 'unknown'), 0),
        4
    ) as remote_share
from marts.dim_jobs
where
    is_open
    [[and role_family = {{role_family}}]]
